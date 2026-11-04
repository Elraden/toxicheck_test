from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.api.identity_dependencies import require_admin, admin_write
from app.db.session import get_annotation_session
from app.schemas.research import StudyInput, StudyState, ValidationInput, LinkCaseInput
from app.services.identity import Principal
from app.services.research_service import ResearchService
from app.services.research_storage import photo_path
from app.services.research_export import write_export

router = APIRouter(dependencies=[Depends(require_admin)])


def filters(study_id=None, participant_id=None, case_id=None, stage=None, validation=None, since=None, until=None, errors=False):
    clauses, params = ['true'], {}
    for column, value in [('study_id', study_id), ('participant_id', participant_id), ('case_id', case_id), ('stage', stage)]:
        if value is not None:
            clauses.append(f'r.{column}=:{column}')
            params[column] = value
    if validation:
        clauses.append('v.status=:validation')
        params['validation'] = validation
    if since:
        clauses.append('r.created_at>=:since')
        params['since'] = since
    if until:
        clauses.append('r.created_at<=:until')
        params['until'] = until
    if errors:
        clauses.append("EXISTS(SELECT 1 FROM research.attempts a WHERE a.run_id=r.id AND a.status IN ('error','needs_retake','interrupted'))")
    return ' AND '.join(clauses), params


def query_filters(study_id: UUID | None = None, participant_id: UUID | None = None, case_id: UUID | None = None,
                  stage: str | None = None, validation: str | None = None,
                  since: datetime | None = None, until: datetime | None = None, errors: bool = False):
    return filters(study_id, participant_id, case_id, stage, validation, since, until, errors)


@router.get('/studies')
async def studies(db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        return [dict(row) for row in (await db.execute(text('SELECT * FROM research.studies ORDER BY created_at DESC'))).mappings()]


@router.post('/studies')
async def create_study(payload: StudyInput, admin: Principal = Depends(admin_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        study_id = uuid4()
        await db.execute(text('''INSERT INTO research.studies(id,title,instruction,scenario,protocol_version)
            VALUES(:id,:title,:instruction,:scenario,:protocol_version)'''), {'id': study_id, **payload.model_dump()})
        await ResearchService(db).audit(admin.user.id, 'study_created', details={'study_id': str(study_id), **payload.model_dump()})
        return {'id': study_id}


@router.put('/studies/{study_id}/state')
async def study_state(study_id: UUID, payload: StudyState, admin: Principal = Depends(admin_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        updated = await db.scalar(text('UPDATE research.studies SET is_open=:open WHERE id=:id RETURNING id'), dict(id=study_id, open=payload.is_open))
        if not updated:
            raise HTTPException(404, 'Исследование не найдено.')
        await ResearchService(db).audit(admin.user.id, 'study_state_changed', details={'study_id': str(study_id), **payload.model_dump()})
        return {'saved': True}


@router.get('/runs')
async def runs(query=Depends(query_filters), offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
               db: AsyncSession = Depends(get_annotation_session)):
    where, params = query
    async with db.begin():
        total = await db.scalar(text('SELECT count(*) FROM research.runs r JOIN research.validations v ON v.run_id=r.id WHERE ' + where), params)
        rows = (await db.execute(text('''SELECT r.id,r.study_id,r.participant_id,r.case_id,r.scenario,r.stage,r.created_at,
            r.manual_duration_ms,r.app_duration_ms,r.labeling_status,c.title AS case_title,s.title AS study_title,v.status AS validation_status,
            (SELECT count(*) FROM research.photos p WHERE p.run_id=r.id) AS photo_count
            FROM research.runs r JOIN research.validations v ON v.run_id=r.id
            JOIN research.cases c ON c.id=r.case_id JOIN research.studies s ON s.id=r.study_id
            WHERE ''' + where + ' ORDER BY r.created_at DESC,r.id LIMIT :limit OFFSET :offset'),
            {**params, 'limit': limit, 'offset': offset})).mappings()
        return {'items': [dict(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}


@router.get('/runs/{run_id}')
async def detail(run_id: UUID, reveal: bool = False, admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        if reveal:
            # A read-access audit, not a modification of the experiment's data.
            if not await db.scalar(text('SELECT id FROM research.runs WHERE id=:id'), dict(id=run_id)):
                raise HTTPException(404, 'Проверка не найдена.')
            await ResearchService(db).audit(admin.user.id, 'analysis_revealed', run_id)
        return await ResearchService(db).detail(run_id, admin=True, reveal=reveal)


@router.put('/runs/{run_id}/validation')
async def validate(run_id: UUID, payload: ValidationInput, admin: Principal = Depends(admin_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        return await ResearchService(db).validate(admin.user.id, run_id, payload)


@router.put('/runs/{run_id}/case')
async def link_case(run_id: UUID, payload: LinkCaseInput, admin: Principal = Depends(admin_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        if not await db.scalar(text('SELECT id FROM research.cases WHERE id=:id'), dict(id=payload.case_id)):
            raise HTTPException(404, 'Этикетка не найдена. Используйте case_id существующей проверки.')
        old = await db.scalar(text('SELECT case_id FROM research.runs WHERE id=:id FOR UPDATE'), dict(id=run_id))
        if not old:
            raise HTTPException(404, 'Проверка не найдена.')
        await db.execute(text('UPDATE research.runs SET case_id=:case,revision=revision+1,updated_at=now() WHERE id=:id'), dict(id=run_id, case=payload.case_id))
        await ResearchService(db).audit(admin.user.id, 'case_linked', run_id, {'before': str(old), 'after': str(payload.case_id)})
        return {'saved': True}


@router.get('/runs/{run_id}/photos/{photo_id}')
async def photo(run_id: UUID, photo_id: UUID, db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        row = (await db.execute(text('SELECT * FROM research.photos WHERE id=:id AND run_id=:run'), dict(id=photo_id, run=run_id))).mappings().first()
        if not row or not photo_path(row['storage_key']).is_file():
            raise HTTPException(404, 'Фото недоступно.')
        return FileResponse(photo_path(row['storage_key']), media_type=row['mime_type'], headers={'X-Content-Type-Options': 'nosniff'})


@router.get('/export')
async def export(query=Depends(query_filters), include_photos: bool = False, excel: bool = True,
                 admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_annotation_session)):
    where, params = query
    async with db.begin():
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        ids = list((await db.scalars(text('SELECT r.id FROM research.runs r JOIN research.validations v ON v.run_id=r.id WHERE ' + where + ' ORDER BY r.created_at,r.id LIMIT 2001'), params)).all())
        if len(ids) > 2000:
            raise HTTPException(422, 'Выгрузка ограничена 2000 проверок. Уточните фильтры; данные не обрезаются автоматически.')
        photos = [dict(row) for row in (await db.execute(text('SELECT * FROM research.photos WHERE run_id=ANY(CAST(:ids AS uuid[]))'), dict(ids=ids))).mappings()]
        if include_photos and sum(p['size_bytes'] for p in photos) > 512 * 1024 * 1024:
            raise HTTPException(422, 'Фото занимают более 512 МБ. Уточните фильтры или выгрузите без фотографий.')
        service = ResearchService(db)
        details = [await service.detail(id, admin=True, reveal=True) for id in ids]
        await service.audit(admin.user.id, 'research_export', details={'count': len(ids), 'include_photos': include_photos, 'filters': params})
    with NamedTemporaryFile(suffix='.zip', delete=False) as file:
        target = Path(file.name)
    try:
        await run_in_threadpool(write_export, target, details, photos, include_photos, excel)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return FileResponse(target, media_type='application/zip', filename='toxicheck-research.zip',
                        background=BackgroundTask(target.unlink, missing_ok=True))
