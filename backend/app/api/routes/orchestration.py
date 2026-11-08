"""Admin-only real-camera orchestration experiment API."""
from uuid import UUID, uuid4
from datetime import datetime
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from app.api.identity_dependencies import require_admin, admin_write, throttle
from app.db.session import AnnotationSessionLocal
from app.services.identity import Principal
from app.services import orchestration as lab
from app.services.research_storage import decode_photo
from app.schemas.orchestration import CaseInput, RunInput, EventsInput, TaskInput, FinishInput, ReviewInput, Mode

async def lab_access(actor:Principal=Depends(require_admin)):
    await throttle('lab:access:'+str(actor.user.id),600,60)
    return actor

router = APIRouter(dependencies=[Depends(lab_access)])

def bounded(value, limit=32768):
    if len(lab.encoded(value).encode()) > limit:
        raise HTTPException(413,'Слишком много данных.')

async def owned(db, run_id, actor, lock=False):
    row = (await db.execute(text('SELECT * FROM orchestration_lab.runs WHERE id=:id AND created_by=:owner' +
        (' FOR UPDATE' if lock else '')),dict(id=run_id,owner=actor.user.id))).mappings().first()
    if not row: raise HTTPException(404,'Запуск не найден.')
    return row

def check_running(row):
    if row['status'] != 'running': raise HTTPException(409,'Запуск завершён.')
    if (lab.now()-row['started_at']).total_seconds()*1000 > row['config']['runMaxMs']+15000:
        raise HTTPException(409,'Время запуска истекло.')

@router.get('/cases')
async def cases(actor: Principal=Depends(require_admin), offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    async with AnnotationSessionLocal() as db:
        return [dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.cases WHERE created_by=:owner ORDER BY created_at DESC LIMIT :limit OFFSET :offset'),
            dict(owner=actor.user.id,limit=limit,offset=offset))).mappings()]

@router.post('/cases')
async def create_case(payload:CaseInput,actor:Principal=Depends(admin_write)):
    await throttle('lab:cases:'+str(actor.user.id),30,60)
    id=uuid4()
    async with AnnotationSessionLocal() as db,db.begin():
        await db.execute(text("""INSERT INTO orchestration_lab.cases(id,created_by,product_label,expected_barcode,label_layout,notes)
            VALUES(:id,:owner,:product_label,:expected_barcode,:label_layout,:notes)"""),
            dict(id=id,owner=actor.user.id,**payload.model_dump()))
    return {'id':id,**payload.model_dump()}

@router.post('/runs')
async def create_run(payload:RunInput,actor:Principal=Depends(admin_write)):
    await throttle('lab:runs:'+str(actor.user.id),30,60)
    bounded(payload.environment,8192)
    async with AnnotationSessionLocal() as db,db.begin():
        await db.execute(text('SELECT pg_advisory_xact_lock(873024121)'))
        previous=(await db.execute(text('SELECT * FROM orchestration_lab.runs WHERE id=:id'),dict(id=payload.id))).mappings().first()
        if previous:
            if previous['created_by']!=actor.user.id or previous['case_id']!=payload.case_id or previous['mode']!=payload.mode or previous['config']!=payload.config.model_dump():
                raise HTTPException(409,'Идентификатор запуска уже использован.')
            return dict(previous)
        if not await db.scalar(text('SELECT id FROM orchestration_lab.cases WHERE id=:id AND created_by=:owner'),dict(id=payload.case_id,owner=actor.user.id)):
            raise HTTPException(404,'Товар не найден.')
        await db.execute(text("""UPDATE orchestration_lab.runs SET status='interrupted',outcome='technical_failure',
            finished_at=now(),summary=summary || '{"interruption":"heartbeat_expired"}'::jsonb
            WHERE status='running' AND heartbeat_at<now()-interval '180 seconds'"""))
        if await db.scalar(text("SELECT EXISTS(SELECT 1 FROM orchestration_lab.runs WHERE status='running') OR EXISTS(SELECT 1 FROM orchestration_lab.ocr_tasks WHERE status='running')")):
            raise HTTPException(409,'Другой запуск или его серверная задача ещё работает.')
        catalog_hash=await db.scalar(text('SELECT input_sha256 FROM catalog.import_runs ORDER BY id DESC LIMIT 1'))
        env={**payload.environment,'catalog_import_sha256':catalog_hash,'app_revision':lab.SERVER_VERSION,'ocr_version':'embedded-adaptive-v1','catalog_schema_version':2,
             'network_path':'server_openfoodfacts','cache_policy':'no_application_cache','client_resources':'not_measured'}
        return dict((await db.execute(text("""INSERT INTO orchestration_lab.runs(id,case_id,mode,protocol_version,config,environment,created_by)
            VALUES(:id,:case,:mode,:protocol,CAST(:config AS jsonb),CAST(:env AS jsonb),:owner) RETURNING *"""),
            dict(id=payload.id,case=payload.case_id,mode=payload.mode,protocol=lab.PROTOCOL,config=lab.encoded(payload.config.model_dump()),
                 env=lab.encoded(env),owner=actor.user.id))).mappings().one())

@router.post('/runs/{run_id}/events')
async def events(run_id:UUID,payload:EventsInput,actor:Principal=Depends(admin_write)):
    await throttle('lab:events:'+str(actor.user.id),120,60)
    bounded(payload.model_dump(),131072)
    async with AnnotationSessionLocal() as db,db.begin():
        await owned(db,run_id,actor,True)
        for event in payload.events:
            bounded(event.details,4096)
            old=(await db.execute(text("SELECT event_name,relative_ms,details FROM orchestration_lab.events WHERE run_id=:id AND source='browser' AND seq=:seq"),
                dict(id=run_id,seq=event.seq))).mappings().first()
            if old:
                if dict(old)!=dict(event_name=event.event_name,relative_ms=event.relative_ms,details=event.details):
                    raise HTTPException(409,'Номер события уже занят другими данными.')
                continue
            await db.execute(text("""INSERT INTO orchestration_lab.events(run_id,seq,event_name,relative_ms,source,details)
                VALUES(:id,:seq,:name,:ms,'browser',CAST(:details AS jsonb))"""),
                dict(id=run_id,seq=event.seq,name=event.event_name,ms=event.relative_ms,details=lab.encoded(event.details)))
        await db.execute(text('UPDATE orchestration_lab.runs SET heartbeat_at=now() WHERE id=:id'),dict(id=run_id))
    return {'saved':True}

async def start_task(run_id:UUID,kind:str,payload:TaskInput,actor:Principal=Depends(admin_write)):
    if kind not in ('ocr','lookup'): raise HTTPException(404)
    await throttle('lab:tasks:'+str(actor.user.id),30,60)
    if kind=='ocr':
        if not payload.image_base64: raise HTTPException(422,'Нужно изображение.')
        from starlette.concurrency import run_in_threadpool
        raw,_=await run_in_threadpool(decode_photo,payload.image_base64)
        digest=hashlib.sha256(raw).hexdigest()
        value=payload.image_base64
    else:
        if not payload.barcode: raise HTTPException(422,'Нужен штрихкод.')
        await throttle('lab:off:global',20,60)
        value=payload.barcode
        digest=hashlib.sha256(value.encode()).hexdigest()
    async with AnnotationSessionLocal() as db,db.begin():
        run=await owned(db,run_id,actor,True)
        existing=(await db.execute(text('SELECT * FROM orchestration_lab.ocr_tasks WHERE id=:id'),dict(id=payload.id))).mappings().first()
        if existing:
            if existing['run_id']!=run_id or existing['kind']!=kind or existing['input_sha256']!=digest:
                raise HTTPException(409,'Идентификатор запроса уже использован.')
            return dict(existing)
        check_running(run)
        await db.execute(text('SELECT pg_advisory_xact_lock(873024122)'))
        if kind=='ocr' and await db.scalar(text("SELECT EXISTS(SELECT 1 FROM orchestration_lab.ocr_tasks WHERE kind='ocr' AND status='running')")):
            raise HTTPException(409,'OCR ещё выполняется.')
        count=await db.scalar(text('SELECT count(*) FROM orchestration_lab.ocr_tasks WHERE run_id=:id AND kind=:kind'),dict(id=run_id,kind=kind))
        if count >= (run['config']['maxOcrRequests'] if kind=='ocr' else 1):
            raise HTTPException(429,'Лимит запросов этого запуска исчерпан.')
        if kind=='ocr':
            recent=await db.scalar(text("""SELECT EXISTS(SELECT 1 FROM orchestration_lab.ocr_tasks WHERE run_id=:id AND kind='ocr'
                AND requested_at>now()-make_interval(secs=>:seconds))"""),
                dict(id=run_id,seconds=run['config']['frameMinIntervalMs']/1000))
            if recent: raise HTTPException(429,'Слишком частый запрос кадра.')
        row=dict((await db.execute(text("""INSERT INTO orchestration_lab.ocr_tasks(id,run_id,kind,status,input_sha256)
            VALUES(:id,:run,:kind,'running',:digest) RETURNING *"""),dict(id=payload.id,run=run_id,kind=kind,digest=digest))).mappings().one())
        await lab.server_event(db,run_id,'ocr_request_started' if kind=='ocr' else 'lookup_started',{'task_id':str(payload.id),'kind':kind})
        lookup_ms=run['config']['lookupMaxMs']
    lab.launch(payload.id,run_id,kind,value,lookup_ms)
    return row

@router.post('/runs/{run_id}/tasks/{task_id}/cancel')
async def cancel_task(run_id:UUID,task_id:UUID,actor:Principal=Depends(admin_write)):
    async with AnnotationSessionLocal() as db,db.begin():
        await owned(db,run_id,actor,True)
        if not await db.scalar(text("""UPDATE orchestration_lab.ocr_tasks SET cancellation=cancellation ||
            jsonb_build_object('cancel_requested',true,'cancel_acknowledged',true,'requested_at',now(),'cancellation_effective',false)
            WHERE id=:task AND run_id=:run RETURNING id"""),dict(task=task_id,run=run_id)):
            raise HTTPException(404)
        await lab.server_event(db,run_id,'cancel_requested',{'task_id':str(task_id),'cancellation_effective':False})
    return {'acknowledged':True,'cancellation_effective':False}

@router.get('/runs/{run_id}/tasks/{task_id}')
async def task_status(run_id:UUID,task_id:UUID,actor:Principal=Depends(require_admin)):
    async with AnnotationSessionLocal() as db:
        await owned(db,run_id,actor)
        row=(await db.execute(text('SELECT * FROM orchestration_lab.ocr_tasks WHERE id=:task AND run_id=:run'),dict(task=task_id,run=run_id))).mappings().first()
        if not row: raise HTTPException(404)
        return dict(row)

@router.post('/runs/{run_id}/finish')
async def finish(run_id:UUID,payload:FinishInput,actor:Principal=Depends(admin_write)):
    bounded(payload.summary)
    async with AnnotationSessionLocal() as db,db.begin():
        run=await owned(db,run_id,actor,True)
        if run['status']!='running': return dict(run)
        valid=payload.outcome in ('valid_barcode','valid_ocr')
        if valid:
            task=(await db.execute(text('SELECT * FROM orchestration_lab.ocr_tasks WHERE id=:id AND run_id=:run'),
                dict(id=payload.winning_task_id,run=run_id))).mappings().first()
            if not task or not task['sufficient'] or task['kind']!=('ocr' if payload.outcome=='valid_ocr' else 'lookup'):
                raise HTTPException(422,'Сервер не подтвердил пригодность результата.')
        summary={'client_observations':payload.summary,'winning_task_id':str(payload.winning_task_id) if payload.winning_task_id else None}
        row=dict((await db.execute(text("""UPDATE orchestration_lab.runs SET status=:status,outcome=:outcome,valid_result=:valid,
            summary=CAST(:summary AS jsonb),finished_at=now() WHERE id=:id RETURNING *"""),
            dict(id=run_id,status='interrupted' if payload.outcome=='cancelled' else 'finished',outcome=payload.outcome,
                 valid=valid,summary=lab.encoded(summary)))).mappings().one())
        await db.execute(text("""UPDATE orchestration_lab.ocr_tasks SET cancellation=cancellation ||
            jsonb_build_object('cancel_requested',true,'cancel_acknowledged',true,'cancellation_effective',false)
            WHERE run_id=:id AND status='running'"""),dict(id=run_id))
        await lab.server_event(db,run_id,'run_finished',{'outcome':payload.outcome,'valid_result':valid})
        return row

@router.put('/runs/{run_id}/review')
async def review(run_id:UUID,payload:ReviewInput,actor:Principal=Depends(admin_write)):
    async with AnnotationSessionLocal() as db,db.begin():
        await owned(db,run_id,actor,True)
        await db.execute(text('UPDATE orchestration_lab.runs SET needs_review=:review WHERE id=:id'),dict(id=run_id,review=payload.needs_review))
        await lab.server_event(db,run_id,'review_changed',{'needs_review':payload.needs_review})
    return {'saved':True}

async def detail(db,id,actor):
    run=dict(await owned(db,id,actor))
    run['events']=[dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.events WHERE run_id=:id ORDER BY source,seq'),dict(id=id))).mappings()]
    run['tasks']=[dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.ocr_tasks WHERE run_id=:id ORDER BY requested_at'),dict(id=id))).mappings()]
    run['case']=dict((await db.execute(text('SELECT * FROM orchestration_lab.cases WHERE id=:id'),dict(id=run['case_id']))).mappings().one())
    run['measured_summary']=lab.measured_summary(run,run['tasks'],run['events'])
    return run

@router.get('/runs/{run_id}')
async def run_detail(run_id:UUID,actor:Principal=Depends(require_admin)):
    async with AnnotationSessionLocal() as db:
        return await detail(db,run_id,actor)

def filter_query(actor,mode,case_id,since,until,outcome):
    where=['created_by=:owner']; params={'owner':actor.user.id}
    for field,value in [('mode',mode),('case_id',case_id),('outcome',outcome)]:
        if value is not None: where.append(field+'=:'+field);params[field]=value
    if since: where.append('started_at>=:since');params['since']=since
    if until: where.append('started_at<=:until');params['until']=until
    return ' AND '.join(where),params

@router.get('/runs')
async def runs(actor:Principal=Depends(require_admin),mode:Mode|None=None,case_id:UUID|None=None,
               since:datetime|None=None,until:datetime|None=None,outcome:str|None=Query(None,max_length=40),
               limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0)):
    where,params=filter_query(actor,mode,case_id,since,until,outcome)
    async with AnnotationSessionLocal() as db:
        total=await db.scalar(text('SELECT count(*) FROM orchestration_lab.runs WHERE '+where),params)
        items=[dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.runs WHERE '+where+
            ' ORDER BY started_at DESC,id LIMIT :limit OFFSET :offset'),dict(**params,limit=limit,offset=offset))).mappings()]
        return {'items':items,'total':total}

@router.get('/runs/{run_id}/export.json')
async def export_one(run_id:UUID,actor:Principal=Depends(require_admin)):
    async with AnnotationSessionLocal() as db,db.begin():
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        return {'schema_version':1,'exported_at':lab.now(),'runs':[await detail(db,run_id,actor)]}

@router.get('/export.json')
async def export_series(actor:Principal=Depends(require_admin),mode:Mode|None=None,case_id:UUID|None=None,
                        since:datetime|None=None,until:datetime|None=None,outcome:str|None=Query(None,max_length=40)):
    where,params=filter_query(actor,mode,case_id,since,until,outcome)
    async with AnnotationSessionLocal() as db,db.begin():
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        ids=list((await db.scalars(text('SELECT id FROM orchestration_lab.runs WHERE '+where+' ORDER BY started_at LIMIT 201'),params)).all())
        if len(ids)>200: raise HTTPException(422,'Уточните фильтры: максимум 200 запусков.')
        result={'schema_version':1,'exported_at':lab.now(),'runs':[]}; size=0
        for id in ids:
            item=await detail(db,id,actor); size+=len(lab.encoded(item).encode())
            if size>32*1024*1024: raise HTTPException(422,'Выгрузка больше 32 МБ. Уточните фильтры.')
            result['runs'].append(item)
        result['aggregates']=lab.aggregate(result['runs'])
        return result

@router.post('/runs/{run_id}/ocr')
async def submit_ocr(run_id:UUID,payload:TaskInput,actor:Principal=Depends(admin_write)):
    return await start_task(run_id,'ocr',payload,actor)

@router.post('/runs/{run_id}/lookup')
async def submit_lookup(run_id:UUID,payload:TaskInput,actor:Principal=Depends(admin_write)):
    return await start_task(run_id,'lookup',payload,actor)

@router.get('/summary')
async def summary(actor:Principal=Depends(require_admin),mode:Mode|None=None,case_id:UUID|None=None,
                  since:datetime|None=None,until:datetime|None=None,outcome:str|None=Query(None,max_length=40)):
    where,params=filter_query(actor,mode,case_id,since,until,outcome)
    async with AnnotationSessionLocal() as db,db.begin():
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        rows=[dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.runs WHERE '+where+' ORDER BY started_at LIMIT 1001'),params)).mappings()]
        if len(rows)>1000: raise HTTPException(422,'Уточните фильтры: сводка ограничена 1000 запусками.')
        if not rows: return {'groups':[]}
        ids=[r['id'] for r in rows]
        tasks=[dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.ocr_tasks WHERE run_id=ANY(CAST(:ids AS uuid[]))'),dict(ids=ids))).mappings()]
        events=[dict(x) for x in (await db.execute(text('SELECT * FROM orchestration_lab.events WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY seq'),dict(ids=ids))).mappings()]
        for row in rows:
            row['measured_summary']=lab.measured_summary(row,[t for t in tasks if t['run_id']==row['id']],[e for e in events if e['run_id']==row['id']])
        return {'groups':lab.aggregate(rows)}
