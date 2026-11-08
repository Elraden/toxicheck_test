import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4, UUID
import httpx
from pydantic import ValidationError
from app.main import create_app
from app.api.identity_dependencies import require_admin
from app.db.session import get_identity_session
from app.schemas.orchestration import Config, RunInput, Event
from app.services.orchestration import sufficient, MeasuredOCR
from app.schemas.ocr import OcrResult

class LabRulesTests(unittest.TestCase):
    def test_configuration_and_mode_bounds(self):
        Config()
        for fields in ({'photoHintMs':4000},{'maxOcrInFlight':2},{'maxOcrRequests':4},{'lookupMaxMs':30000}):
            with self.assertRaises(ValidationError): Config(**fields)
        with self.assertRaises(ValidationError):
            RunInput(id=uuid4(),case_id=uuid4(),mode='photo_parallel')
        with self.assertRaises(ValidationError):
            Event(seq=0,event_name='run_started',relative_ms=float('nan'))

    def test_sufficient_uses_actual_composition_gate(self):
        for status,text in [('success',''),('needs_retake','сахар, соль'),('error','сахар, соль'),('success',None)]:
            self.assertFalse(sufficient(status,text))
        self.assertTrue(sufficient('success','Состав: сахар, соль, вода, мука пшеничная.'))

    def test_measurement_is_shared_process_not_claimed_exact_cpu(self):
        worker=MeasuredOCR()
        with patch('app.services.ocr_service.OcrService._recognize_embedded_sync',return_value=OcrResult(status='needs_retake')):
            result=worker._recognize_embedded_sync('x','lab')
        self.assertEqual(result.status,'needs_retake')
        self.assertEqual(worker.measurements['measurement_quality'],'shared_process_estimate')
        self.assertGreaterEqual(worker.measurements['ocr_worker_ms'],0)
        self.assertNotIn('cpu_time_ms',worker.measurements)

    def test_measurement_survives_worker_error(self):
        worker=MeasuredOCR()
        with patch('app.services.ocr_service.OcrService._recognize_embedded_sync',side_effect=ValueError):
            with self.assertRaises(ValueError):worker._recognize_embedded_sync('x','lab')
        self.assertIsNotNone(worker.measurements['worker_finished_at'])

class LabAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app=create_app()
        limiter=patch('app.api.routes.orchestration.throttle',AsyncMock());limiter.start();self.addCleanup(limiter.stop)
        async def fake_session():yield AsyncMock()
        self.app.dependency_overrides[get_identity_session]=fake_session
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_guest_cannot_read_or_write_any_experiment_resource(self):
        id=str(uuid4())
        paths=[('GET','/cases'),('POST','/cases'),('GET','/runs'),('POST','/runs'),
               ('GET',f'/runs/{id}'),('POST',f'/runs/{id}/events'),('POST',f'/runs/{id}/finish'),
               ('POST',f'/runs/{id}/ocr'),('POST',f'/runs/{id}/lookup'),('GET','/export.json')]
        with patch('app.services.identity.IdentityService.read',new=AsyncMock(return_value=None)):
            for method,path in paths:
                response=await self.client.request(method,'/api/admin/orchestration'+path,json={})
                self.assertEqual(response.status_code,403,(path,response.text))

    async def test_admin_without_csrf_cannot_write(self):
        self.app.dependency_overrides[require_admin]=lambda:SimpleNamespace(user=SimpleNamespace(id=uuid4()),csrf='secret')
        response=await self.client.post('/api/admin/orchestration/cases',json={})
        self.assertEqual(response.status_code,403)

import os
import base64
from io import BytesIO
from pathlib import Path
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.db.initialize import SCHEMAS, DB_DIR

@unittest.skipUnless(os.environ.get('TOXICHECK_BOOTSTRAP_TEST')=='1','Requires isolated loopback test database on 55440')
class LabDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin=await asyncpg.connect(host='127.0.0.1',port=55440,user='bootstrap_test',database='postgres')
        self.dbname='bootstrap_test_'+uuid4().hex
        await self.admin.execute(f'CREATE DATABASE {self.dbname}')
        self.addAsyncCleanup(self.cleanup_database)
        self.conn=await asyncpg.connect(host='127.0.0.1',port=55440,user='bootstrap_test',database=self.dbname)
        self.addAsyncCleanup(self.conn.close)
        for name in SCHEMAS: await self.conn.execute((DB_DIR/name).read_text(encoding='utf-8'))
        self.uid=await self.conn.fetchval("INSERT INTO public.users(role) VALUES('admin') RETURNING id")
        engine=create_async_engine(f'postgresql+asyncpg://bootstrap_test@127.0.0.1:55440/{self.dbname}')
        self.addAsyncCleanup(engine.dispose)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        self.app=create_app()
        from app.api.identity_dependencies import admin_write
        self.principal=SimpleNamespace(user=SimpleNamespace(id=self.uid),csrf='secret')
        self.app.dependency_overrides[require_admin]=lambda:self.principal
        self.app.dependency_overrides[admin_write]=lambda:self.principal
        self.patches=[
            patch('app.api.routes.orchestration.AnnotationSessionLocal',sessions),
            patch('app.services.orchestration.AnnotationSessionLocal',sessions),
            patch('app.api.routes.orchestration.throttle',AsyncMock()),
            patch('app.services.orchestration.launch')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test/api/admin/orchestration')
        self.addAsyncCleanup(self.client.aclose)
        r=await self.client.post('/cases',json={'product_label':'Тест','label_layout':'same_side'})
        self.assertEqual(r.status_code,200,r.text);self.case=r.json()['id']
        self.id=str(uuid4())
        r=await self.client.post('/runs',json={'id':self.id,'case_id':self.case,'mode':'video_adaptive'})
        self.assertEqual(r.status_code,200,r.text)

    async def cleanup_database(self):
        assert self.dbname.startswith('bootstrap_test_') and len(self.dbname)==47
        await self.admin.execute(f'DROP DATABASE {self.dbname} WITH (FORCE)')
        await self.admin.close()

    async def test_idempotent_events_and_finish_preserve_late_diagnostics(self):
        event={'seq':0,'event_name':'run_started','relative_ms':0,'details':{}}
        for _ in range(2):
            r=await self.client.post(f'/runs/{self.id}/events',json={'events':[event]})
            self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM orchestration_lab.events WHERE source='browser'"),1)
        r=await self.client.post(f'/runs/{self.id}/events',json={'events':[{**event,'relative_ms':1}]})
        self.assertEqual(r.status_code,409)
        for outcome in ['timeout','valid_ocr']:
            r=await self.client.post(f'/runs/{self.id}/finish',json={'outcome':outcome})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['outcome'],'timeout')
        r=await self.client.post(f'/runs/{self.id}/events',json={'events':[{**event,'seq':1,'event_name':'ocr_worker_observed','relative_ms':5000}]})
        self.assertEqual(r.status_code,200)
        r=await self.client.get(f'/runs/{self.id}/export.json')
        self.assertEqual(r.status_code,200,r.text);data=r.json()
        self.assertEqual(data['schema_version'],1)
        self.assertFalse(data['runs'][0]['valid_result'])
        self.assertEqual(len(data['runs'][0]['events']),3)

    async def test_server_does_not_trust_client_success_and_task_ids_are_idempotent(self):
        r=await self.client.post(f'/runs/{self.id}/finish',json={'outcome':'valid_barcode','summary':{'cpu_time_ms':0}})
        self.assertEqual(r.status_code,422,r.text)
        tid=str(uuid4());payload={'id':tid,'barcode':'4600000000001'}
        for _ in range(2):
            r=await self.client.post(f'/runs/{self.id}/lookup',json=payload)
            self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.ocr_tasks'),1)
        r=await self.client.post(f'/runs/{self.id}/tasks/{tid}/cancel',json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['cancellation_effective'])
        await self.conn.execute("UPDATE orchestration_lab.ocr_tasks SET sufficient=true,status='finished' WHERE id=$1",UUID(tid))
        r=await self.client.post(f'/runs/{self.id}/finish',json={'outcome':'valid_barcode','winning_task_id':tid})
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['valid_result'])
        r=await self.client.get('/export.json')
        self.assertEqual(r.status_code,200,r.text)

    async def test_ocr_limits_actual_images_and_pending_worker(self):
        from PIL import Image
        buffer=BytesIO();Image.new('RGB',(640,480),'white').save(buffer,format='JPEG')
        image=base64.b64encode(buffer.getvalue()).decode()
        tid=str(uuid4())
        r=await self.client.post(f'/runs/{self.id}/ocr',json={'id':tid,'image_base64':image})
        self.assertEqual(r.status_code,200,r.text)
        r=await self.client.post(f'/runs/{self.id}/ocr',json={'id':str(uuid4()),'image_base64':image})
        self.assertEqual(r.status_code,409,r.text)
        r=await self.client.post(f'/runs/{self.id}/finish',json={'outcome':'timeout'})
        self.assertEqual(r.status_code,200)
        row=await self.conn.fetchrow('SELECT * FROM orchestration_lab.ocr_tasks WHERE id=$1',UUID(tid))
        self.assertEqual(row['status'],'running')
        self.assertIsNone(row['cpu_time_ms'])
        r=await self.client.post('/runs',json={'id':str(uuid4()),'case_id':self.case,'mode':'photo_sequential'})
        self.assertEqual(r.status_code,409,r.text)

    async def test_migration_repeat_keeps_existing_research_and_restart_marks_interrupted(self):
        study=uuid4()
        await self.conn.execute("INSERT INTO research.studies(id,title,instruction,scenario) VALUES($1,'keep','keep','comparison')",study)
        await self.conn.execute((DB_DIR/'migrations/20261108_orchestration_experiments.sql').read_text())
        self.assertEqual(await self.conn.fetchval('SELECT title FROM research.studies WHERE id=$1',study),'keep')
        from app.services.orchestration import recover
        await recover()
        row=await self.conn.fetchrow('SELECT status,outcome FROM orchestration_lab.runs WHERE id=$1',UUID(self.id))
        self.assertEqual(row['status'],'interrupted')
        self.assertEqual(row['outcome'],'technical_failure')

    async def test_other_admin_cannot_read_write_or_export_this_run(self):
        self.principal=SimpleNamespace(user=SimpleNamespace(id=uuid4()),csrf='other')
        for method,path,payload in [('GET',f'/runs/{self.id}',None),('POST',f'/runs/{self.id}/events',{'events':[]}),
                                    ('GET',f'/runs/{self.id}/export.json',None)]:
            r=await self.client.request(method,path,json=payload)
            self.assertEqual(r.status_code,404,r.text)

    async def test_worker_finishes_after_run_and_retains_null_exact_cpu(self):
        from app.services import orchestration as lab
        tid=str(uuid4())
        from PIL import Image
        buffer=BytesIO();Image.new('RGB',(640,480),'white').save(buffer,format='JPEG')
        image=base64.b64encode(buffer.getvalue()).decode()
        r=await self.client.post(f'/runs/{self.id}/ocr',json={'id':tid,'image_base64':image})
        self.assertEqual(r.status_code,200,r.text)
        r=await self.client.post(f'/runs/{self.id}/finish',json={'outcome':'timeout'})
        self.assertEqual(r.status_code,200,r.text)
        async def recognize(worker,value,source):
            worker.measurements={'worker_started_at':lab.now(),'worker_finished_at':lab.now(),
                'ocr_worker_ms':2,'shared_process_cpu_delta_ms':1,'measurement_quality':'shared_process_estimate'}
            return OcrResult(status='needs_retake')
        with patch.object(lab.MeasuredOCR,'recognize_image',recognize):
            await lab.perform(UUID(tid),UUID(self.id),'ocr',image,8000)
        r=await self.client.get(f'/runs/{self.id}')
        self.assertEqual(r.status_code,200,r.text)
        record=r.json()
        self.assertEqual(record['outcome'],'timeout')
        self.assertIsNone(record['tasks'][0]['cpu_time_ms'])
        self.assertEqual(record['tasks'][0]['status'],'finished')
        self.assertTrue(record['measured_summary']['worker_stopped_after_run'])
        self.assertFalse(record['measured_summary']['pending_worker'])
        self.assertIsNone(record['measured_summary']['end_to_end_ms'])
        r=await self.client.get('/summary')
        self.assertEqual(r.status_code,200,r.text)
        group=r.json()['groups'][0]
        self.assertEqual(group['n'],1)
        self.assertEqual(group['success'],0)
        self.assertIsNone(group['p50'])
        self.assertIsNone(group['ocr_cpu_time_ms'])
        self.assertEqual(group['shared_process_cpu_per_run_ms'],1)


    async def test_unavailable_ocr_does_not_claim_worker_completion(self):
        from app.services import orchestration as lab
        tid=uuid4()
        await self.conn.execute("INSERT INTO orchestration_lab.ocr_tasks(id,run_id,kind,status,input_sha256) VALUES($1,$2,'ocr','running','test')",tid,UUID(self.id))
        with patch.object(lab.MeasuredOCR,'recognize_image',new=AsyncMock(return_value=OcrResult(status='ocr_unavailable'))):
            await lab.perform(tid,UUID(self.id),'ocr',base64.b64encode(b'x').decode(),8000)
        r=await self.client.get(f'/runs/{self.id}')
        self.assertEqual(r.status_code,200,r.text)
        data=r.json()
        names=[e['event_name'] for e in data['events']]
        self.assertIn('ocr_request_finished',names)
        self.assertNotIn('ocr_worker_finished',names)
        self.assertIsNone(data['tasks'][0]['worker_finished_at'])
        self.assertIsNone(data['measured_summary']['ocr_worker_ms'])
