import asyncio
import hashlib
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.identity_dependencies import guest_user, guest_write, throttle
from app.core.config import settings
from app.db.session import get_annotation_session
from app.schemas.analysis import AnalysisPreferences
from app.schemas.research import RunInput, StageInput, PhotoInput, AttemptInput, DisplayInput
from app.services.identity import Principal
from app.services.research_service import ResearchService, encoded, catalog_fingerprint
from app.services.research_storage import decode_photo, photo_path, store_photo, prepared_image
from app.services.ocr_service import OcrService
from app.services.analysis_service import AnalysisService

router = APIRouter()


@router.get('/studies')
async def studies(user: Principal = Depends(guest_user), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        return [dict(row) for row in (await db.execute(text('SELECT * FROM research.studies WHERE is_open ORDER BY created_at DESC'))).mappings()]


@router.get('/runs')
async def runs(user: Principal = Depends(guest_user), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        return [dict(row) for row in (await db.execute(text('''SELECT r.id,r.stage,r.created_at,r.scenario,c.title,s.title AS study_title
            FROM research.runs r JOIN research.participants p ON p.id=r.participant_id
            JOIN research.cases c ON c.id=r.case_id JOIN research.studies s ON s.id=r.study_id
            WHERE p.user_id=:user ORDER BY r.created_at DESC LIMIT 100'''), dict(user=user.user.id))).mappings()]


@router.post('/runs')
async def create_run(payload: RunInput, user: Principal = Depends(guest_write), db: AsyncSession = Depends(get_annotation_session)):
    await throttle('research-create:' + str(user.user.id), 30, 3600)
    async with db.begin():
        return await ResearchService(db).create(user.user.id, payload)


@router.get('/runs/{run_id}')
async def read_run(run_id: UUID, user: Principal = Depends(guest_user), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        return await ResearchService(db).detail(run_id, user.user.id)


@router.post('/runs/{run_id}/stage')
async def change_stage(run_id: UUID, payload: StageInput, user: Principal = Depends(guest_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        return await ResearchService(db).change_stage(user.user.id, run_id, payload)


@router.post('/runs/{run_id}/photos')
async def upload_photo(run_id: UUID, payload: PhotoInput, user: Principal = Depends(guest_write), db: AsyncSession = Depends(get_annotation_session)):
    await throttle('research-photo:' + str(user.user.id), 30, 3600)
    async with db.begin():
        run = await ResearchService(db).owned(run_id, user.user.id, lock=True)
        if run['stage'] not in ('app_active', 'labeling'):
            raise HTTPException(409, 'Фото доступно после ручного этапа и запуска проверки ToxiCheck.')
        raw, mime = await run_in_threadpool(decode_photo, payload.image_base64)
        digest = hashlib.sha256(raw).hexdigest()
        previous = (await db.execute(text('SELECT * FROM research.photos WHERE id=:id'), dict(id=payload.id))).mappings().first()
        if previous:
            if previous['run_id'] != run_id or previous['sha256'] != digest:
                raise HTTPException(409, 'Идентификатор фото уже использован.')
            return {'id': previous['id']}
        if await db.scalar(text('SELECT count(*) FROM research.photos WHERE run_id=:id'), dict(id=run_id)) >= 10:
            raise HTTPException(422, 'Для одной проверки можно сохранить до 10 фотографий.')
        key = await run_in_threadpool(store_photo, payload.id, raw)
        await db.execute(text('''INSERT INTO research.photos(id,run_id,storage_key,mime_type,sha256,size_bytes)
            VALUES(:id,:run,:key,:mime,:sha,:size)'''),
            dict(id=payload.id, run=run_id, key=key, mime=mime, sha=digest, size=len(raw)))
        return {'id': payload.id}


@router.get('/runs/{run_id}/photos/{photo_id}')
async def view_photo(run_id: UUID, photo_id: UUID, user: Principal = Depends(guest_user), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        await ResearchService(db).owned(run_id, user.user.id)
        row = (await db.execute(text('SELECT * FROM research.photos WHERE id=:id AND run_id=:run'), dict(id=photo_id, run=run_id))).mappings().first()
        if not row or not photo_path(row['storage_key']).is_file():
            raise HTTPException(404, 'Фото недоступно.')
        return FileResponse(photo_path(row['storage_key']), media_type=row['mime_type'], headers={'X-Content-Type-Options': 'nosniff'})


@router.post('/runs/{run_id}/attempts')
async def analyze_photo(run_id: UUID, payload: AttemptInput, user: Principal = Depends(guest_write), db: AsyncSession = Depends(get_annotation_session)):
    await throttle('research-ocr:' + str(user.user.id), 20, 3600)
    started = time.perf_counter()
    async with db.begin():
        run = await ResearchService(db).owned(run_id, user.user.id, lock=True)
        previous = (await db.execute(text('SELECT * FROM research.attempts WHERE id=:id'), dict(id=payload.id))).mappings().first()
        if previous:
            if previous['run_id'] != run_id or previous['photo_id'] != payload.photo_id:
                raise HTTPException(409, 'Идентификатор попытки уже использован.')
            return dict(previous)
        if run['stage'] != 'app_active':
            raise HTTPException(409, 'Сначала завершите ручной этап и начните проверку ToxiCheck.')
        photo = (await db.execute(text('SELECT * FROM research.photos WHERE id=:id AND run_id=:run'), dict(id=payload.photo_id, run=run_id))).mappings().first()
        if not photo:
            raise HTTPException(404, 'Фотография не найдена.')
        if await db.scalar(text("SELECT count(*) FROM research.attempts WHERE run_id=:id AND status='processing'"), dict(id=run_id)):
            raise HTTPException(409, 'Предыдущая попытка ещё выполняется. Дождитесь её или отметьте как прерванную.')
        if await db.scalar(text('SELECT count(*) FROM research.attempts WHERE run_id=:id'), dict(id=run_id)) >= 20:
            raise HTTPException(422, 'Достигнут лимит попыток для проверки.')
        await db.execute(text('''INSERT INTO research.attempts(id,run_id,photo_id,status,app_version,pipeline_version,catalog_version,capture_source)
            VALUES(:id,:run,:photo,'processing',:app,:pipeline,'pending',:source)'''),
            dict(id=payload.id, run=run_id, photo=payload.photo_id, app=settings.research_app_version,
                 pipeline=settings.research_pipeline_version, source=payload.capture_source))
    # The attempt is committed before the expensive operation. A process crash leaves a recoverable record.
    status, error, result, fingerprint = 'error', None, None, 'unavailable'
    try:
        image = await run_in_threadpool(prepared_image, photo['storage_key'])
        ocr = await asyncio.wait_for(OcrService().recognize_image(image, source=payload.capture_source), timeout=120)
        result = {'raw_ingredient_text': ocr.raw_text, 'ingredients_text': ocr.composition_text,
                  'confidence': ocr.confidence, 'ocr_processing_ms': ocr.processing_time_ms,
                  'analysis': None, 'app_detected_e_codes': [], 'matched_restrictions': []}
        status = 'success' if ocr.status == 'success' else 'needs_retake' if ocr.status == 'needs_retake' else 'error'
        error = None if status == 'success' else (ocr.message or 'Состав не распознан')
        if status == 'success' and not (ocr.composition_text or '').strip():
            status, error = 'needs_retake', 'Состав не определён'
        if status == 'success':
            async with db.begin():
                await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
                if not await db.scalar(text('SELECT EXISTS(SELECT 1 FROM catalog.ingredients WHERE is_active)')):
                    raise HTTPException(503, 'Справочник ингредиентов пока не загружен.')
                fingerprint = await catalog_fingerprint(db)
                result['rules_evaluated_on'] = str(await db.scalar(text('SELECT CURRENT_DATE')))
                analysis = await AnalysisService(db).analyze(ocr.composition_text, AnalysisPreferences(
                    excludedIngredientIds=[item['ingredient_id'] for item in run['exclusions']]))
                result['analysis'] = analysis.model_dump(mode='json', by_alias=True)
                codes = sorted({item.code.upper() for item in analysis.matched if item.code})
                result['app_detected_e_codes'] = codes
                result['matched_restrictions'] = sorted(set(codes) & {item['e_code'] for item in run['exclusions']})
    except HTTPException as exc:
        status, error = 'error', str(exc.detail)[:1000]
    except Exception as exc:
        status, error = 'error', 'Не удалось выполнить анализ: ' + type(exc).__name__
    async with db.begin():
        await db.execute(text('''UPDATE research.attempts SET status=:status,error=:error,result=CAST(:result AS jsonb),
            server_processing_ms=:ms,catalog_version=:catalog,finished_at=now() WHERE id=:id AND status='processing' ''') ,
            dict(id=payload.id, status=status, error=error, result=encoded(result), catalog=fingerprint,
                 ms=round((time.perf_counter() - started) * 1000)))
        return dict((await db.execute(text('SELECT * FROM research.attempts WHERE id=:id'), dict(id=payload.id))).mappings().one())


@router.post('/runs/{run_id}/attempts/{attempt_id}/displayed')
async def displayed(run_id: UUID, attempt_id: UUID, payload: DisplayInput, user: Principal = Depends(guest_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        run = await ResearchService(db).owned(run_id, user.user.id, lock=True)
        if run['stage'] != 'app_active':
            raise HTTPException(409, 'Этап анализа уже закрыт.')
        row = (await db.execute(text('SELECT * FROM research.attempts WHERE id=:id AND run_id=:run FOR UPDATE'), dict(id=attempt_id, run=run_id))).mappings().first()
        if not row or row['status'] == 'processing':
            raise HTTPException(409, 'Результат ещё не готов.')
        if row['app_e2e_ms'] is not None and row['app_e2e_ms'] != payload.app_e2e_ms:
            raise HTTPException(409, 'Замер уже зафиксирован.')
        if row['server_processing_ms'] is not None and payload.app_e2e_ms + 1000 < row['server_processing_ms']:
            raise HTTPException(422, 'Замер меньше времени обработки сервером.')
        await db.execute(text('''UPDATE research.attempts SET app_e2e_ms=:ms,displayed_at=COALESCE(displayed_at,now())
            WHERE id=:id'''), dict(id=attempt_id, ms=payload.app_e2e_ms))
        return {'saved': True}


@router.post('/runs/{run_id}/attempts/{attempt_id}/recover')
async def recover(run_id: UUID, attempt_id: UUID, user: Principal = Depends(guest_write), db: AsyncSession = Depends(get_annotation_session)):
    async with db.begin():
        run = await ResearchService(db).owned(run_id, user.user.id, lock=True)
        if run['stage'] != 'app_active':
            raise HTTPException(409, 'Этап анализа уже закрыт.')
        # No fabricated E2E after reload: mark visibility, retain NULL technical timing.
        row = (await db.execute(text('''UPDATE research.attempts SET
            error=CASE WHEN status='processing' THEN 'Прервано участником после потери связи' ELSE error END,
            status=CASE WHEN status='processing' THEN 'interrupted' ELSE status END,
            finished_at=COALESCE(finished_at,now()),displayed_at=COALESCE(displayed_at,now())
            WHERE id=:id AND run_id=:run RETURNING id'''), dict(id=attempt_id, run=run_id))).first()
        if not row:
            raise HTTPException(404, 'Попытка не найдена.')
        await ResearchService(db).audit(user.user.id, 'attempt_recovered_without_new_timing', run_id, {'attempt_id': str(attempt_id)})
        return await ResearchService(db).detail(run_id, user.user.id)
