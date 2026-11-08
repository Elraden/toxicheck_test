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

    def test_aggregates_ignore_excluded_metrics_even_in_full_export(self):
        from app.services.orchestration import aggregate
        def row(ms,cpu,calls,excluded=False):
            return dict(environment={},protocol_version='test',mode='video_parallel',config={},
                status='finished',valid_result=True,excluded_from_analysis=excluded,
                measured_summary=dict(end_to_end_ms=ms,shared_process_cpu_delta_ms=cpu,
                    shared_process_rss_peak_bytes=cpu*100,ocr_calls=calls,api_calls=calls))
        group=aggregate([row(100,10,1),row(200,20,2),row(99999,99999,99,True)])[0]
        self.assertEqual((group['n'],group['success'],group['p50'],group['p95']),(2,2,100,200))
        self.assertEqual((group['ocr_calls'],group['api_calls']),(3,3))
        self.assertEqual(group['shared_process_cpu_per_run_ms'],15)
        self.assertEqual(group['shared_process_cpu_per_success_ms'],15)
        self.assertEqual(group['shared_process_rss_peak_bytes'],2000)
        self.assertEqual(aggregate([row(1,1,1,True)]),[])

    def test_sufficient_uses_actual_composition_gate(self):
        for status,text in [('success',''),('needs_retake','сахар, соль'),('error','сахар, соль'),('success',None)]:
            self.assertFalse(sufficient(status,text))
        self.assertTrue(sufficient('success','Состав: сахар, соль, вода, мука пшеничная.'))

    def test_measurement_is_shared_process_not_claimed_exact_cpu(self):
        worker=MeasuredOCR()
        with patch('app.services.ocr_service.OcrService._recognize_embedded_sync',return_value=OcrResult(status='needs_retake')):
            result=worker._recognize_embedded_sync('x','lab')
        self.assertEqual(result.status,'needs_retake')
        self.assertEqual(worker.measurements['measurement_quality'],'child_not_observed')
        self.assertIsNone(worker.measurements['tesseract_child_cpu_time_ms'])
        self.assertIsNone(worker.measurements['tesseract_child_rss_peak_bytes'])
        self.assertGreaterEqual(worker.measurements['ocr_python_thread_cpu_ms'],0)
        self.assertGreaterEqual(worker.measurements['ocr_worker_ms'],0)
        self.assertNotIn('cpu_time_ms',worker.measurements)

    def test_resource_aggregate_uses_reviewed_full_frames_not_ocr_recall(self):
        from app.services.orchestration import aggregate
        def row(complete,ocr_quality):
            return dict(environment={},protocol_version='live-v3.2',mode='video_parallel',config={},
                status='finished',valid_result=True,excluded_from_analysis=False,
                measured_summary=dict(end_to_end_ms=5000,shared_process_cpu_delta_ms=50,
                    shared_process_rss_peak_bytes=1000000,ocr_calls=1,api_calls=1,
                    ocr_cpu_time_ms=90,total_transfer_bytes=4000,ocr_image_bytes=2500,
                    frame_complete=complete,quality_reviewed=True,quality_success=ocr_quality))
        group=aggregate([row(False,True),row(True,False)])[0]
        self.assertEqual(group['complete_frames'],1)
        self.assertEqual(group['frame_completeness_rate'],0.5)
        self.assertEqual(group['ocr_cpu_time_ms'],180)
        self.assertEqual(group['bytes_per_complete_scan'],8000)
        self.assertEqual(group['ocr_cpu_per_complete_scan_ms'],180)
        unreviewed=aggregate([row(None,True)])[0]
        self.assertIsNone(unreviewed['bytes_per_complete_scan'])

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
        paths=[('GET','/cases'),('POST','/cases'),('GET','/runs'),('POST','/runs'),('DELETE',f'/runs/{id}'),
               ('GET',f'/runs/{id}'),('POST',f'/runs/{id}/events'),('POST',f'/runs/{id}/finish'),
               ('POST',f'/runs/{id}/ocr'),('POST',f'/runs/{id}/lookup'),('GET','/export.json'),('PUT',f'/runs/{id}/exclusion')]
        with patch('app.services.identity.IdentityService.read',new=AsyncMock(return_value=None)):
            for method,path in paths:
                response=await self.client.request(method,'/api/admin/orchestration'+path,json={})
                self.assertEqual(response.status_code,403,(path,response.text))

    async def test_admin_without_csrf_cannot_write(self):
        self.app.dependency_overrides[require_admin]=lambda:SimpleNamespace(user=SimpleNamespace(id=uuid4()),csrf='secret')
        response=await self.client.post('/api/admin/orchestration/cases',json={})
        self.assertEqual(response.status_code,403)
        response=await self.client.put('/api/admin/orchestration/runs/'+str(uuid4())+'/exclusion',json={'excluded':True,'reason':'bad_setup'})
        self.assertEqual(response.status_code,403)
        response=await self.client.delete('/api/admin/orchestration/runs/'+str(uuid4()))
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
                                    ('GET',f'/runs/{self.id}/export.json',None),
                                    ('PUT',f'/runs/{self.id}/exclusion',{'excluded':True,'reason':'bad_setup'}),
                                    ('DELETE',f'/runs/{self.id}',None)]:
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


    async def test_exclusion_restore_audit_and_exports_preserve_results(self):
        ids=[self.id]
        for i in range(3):
            if i:
                rid=str(uuid4())
                response=await self.client.post('/runs',json={'id':rid,'case_id':self.case,'mode':'video_adaptive'})
                self.assertEqual(response.status_code,200,response.text);ids.append(rid)
            response=await self.client.post(f'/runs/{ids[-1]}/finish',json={'outcome':'timeout'})
            self.assertEqual(response.status_code,200,response.text)
        endpoint=f'/runs/{self.id}'
        before=(await self.client.get(endpoint)).json()
        payload={'excluded':True,'reason':'operator_mistake','comment':'Неверный товар в кадре'}
        responses=await asyncio.gather(*(self.client.put(endpoint+'/exclusion',json=payload) for _ in range(2)))
        for r in responses:self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(responses[0].json()['excluded_at'],responses[1].json()['excluded_at'])
        data=(await self.client.get(endpoint)).json()
        self.assertEqual(len(data['exclusion_audit']),1)
        for field in ['outcome','valid_result','finished_at','summary','measured_summary','events','tasks']:
            self.assertEqual(data[field],before[field],field)
        summary=(await self.client.get('/summary')).json()
        self.assertEqual((summary['n_total'],summary['n_excluded']),(3,1))
        self.assertEqual(sum(g['n'] for g in summary['groups']),2)
        self.assertEqual((await self.client.get('/runs')).json()['total'],2)
        self.assertEqual((await self.client.get('/runs?exclusion_status=excluded')).json()['total'],1)
        self.assertEqual((await self.client.get('/runs?exclusion_status=all')).json()['total'],3)
        self.assertEqual(len((await self.client.get('/export.json')).json()['runs']),2)
        full=(await self.client.get('/export.json?include_excluded=true')).json()
        self.assertEqual(len(full['runs']),3)
        self.assertEqual(sum(g['n'] for g in full['aggregates']),2)
        self.assertEqual((await self.client.get(endpoint+'/export.json')).status_code,404)
        self.assertEqual((await self.client.get(endpoint+'/export.json?include_excluded=true')).status_code,200)
        for _ in range(2):
            r=await self.client.put(endpoint+'/exclusion',json={'excluded':False})
            self.assertEqual(r.status_code,200,r.text)
        restored=(await self.client.get(endpoint)).json()
        self.assertEqual([a['action'] for a in restored['exclusion_audit']],['exclude','restore'])
        self.assertEqual(restored['exclusion_audit'][1]['reason'],'operator_mistake')
        self.assertEqual(restored['exclusion_audit'][1]['comment'],payload['comment'])
        self.assertIsNone(restored['excluded_at'])
        for field in ['outcome','valid_result','finished_at','summary','measured_summary','events','tasks']:
            self.assertEqual(restored[field],before[field],field)
        summary=(await self.client.get('/summary')).json()
        self.assertEqual((summary['n_total'],summary['n_excluded']),(3,0))
        self.assertEqual(sum(g['n'] for g in summary['groups']),3)
        for name in SCHEMAS:await self.conn.execute((DB_DIR/name).read_text(encoding='utf-8'))
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.exclusion_audit'),2)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.runs'),3)

    async def test_exclusion_requires_reason_and_finished_workers(self):
        endpoint=f'/runs/{self.id}/exclusion'
        for payload in [{'excluded':True},{'excluded':True,'reason':'other','comment':'  '},
                        {'excluded':True,'reason':'ocr_failed'},{'excluded':False,'reason':'bad_setup'}]:
            self.assertEqual((await self.client.put(endpoint,json=payload)).status_code,422)
        payload={'excluded':True,'reason':'other','comment':'Ошибочно выбран режим'}
        self.assertEqual((await self.client.put(endpoint,json=payload)).status_code,409)
        tid=uuid4()
        await self.conn.execute("INSERT INTO orchestration_lab.ocr_tasks(id,run_id,kind,status,input_sha256) VALUES($1,$2,'ocr','running','test')",tid,UUID(self.id))
        await self.client.post(f'/runs/{self.id}/finish',json={'outcome':'timeout'})
        self.assertEqual((await self.client.put(endpoint,json=payload)).status_code,409)
        await self.conn.execute("UPDATE orchestration_lab.ocr_tasks SET status='interrupted' WHERE id=$1",tid)
        response=await self.client.put(endpoint,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.exclusion_audit'),1)

    async def test_permanent_delete_removes_entire_trial_and_recalculates_summaries(self):
        endpoint=f'/runs/{self.id}'
        # Never delete an active experiment.
        self.assertEqual((await self.client.delete(endpoint)).status_code,409)
        tid=uuid4()
        await self.conn.execute("INSERT INTO orchestration_lab.ocr_tasks(id,run_id,kind,status,input_sha256) VALUES($1,$2,'ocr','running','test')",tid,UUID(self.id))
        await self.client.post(endpoint+'/finish',json={'outcome':'timeout'})
        # Run is finished, but the OCR worker is still running.
        self.assertEqual((await self.client.delete(endpoint)).status_code,409)
        await self.conn.execute("UPDATE orchestration_lab.ocr_tasks SET status='finished' WHERE id=$1",tid)
        await self.client.post(endpoint+'/events',json={'events':[{'seq':0,'event_name':'run_started','relative_ms':0,'details':{}}]})
        await self.client.put(endpoint+'/exclusion',json={'excluded':True,'reason':'operator_mistake','comment':'Сбой камеры'})
        other=str(uuid4())
        self.assertEqual((await self.client.post('/runs',json={'id':other,'case_id':self.case,'mode':'photo_adaptive'})).status_code,200)
        self.assertEqual((await self.client.post('/runs/'+other+'/finish',json={'outcome':'timeout'})).status_code,200)
        before=(await self.client.get('/summary')).json()
        self.assertEqual((before['n_total'],before['n_excluded']),(2,1))
        response=await self.client.delete(endpoint)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{'deleted':True,'id':self.id})
        self.assertEqual((await self.client.get(endpoint)).status_code,404)
        self.assertEqual((await self.client.delete(endpoint)).status_code,404)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.ocr_tasks WHERE run_id=$1',UUID(self.id)),0)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.events WHERE run_id=$1',UUID(self.id)),0)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.exclusion_audit WHERE run_id=$1',UUID(self.id)),0)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM orchestration_lab.runs WHERE id=$1',UUID(self.id)),0)
        self.assertEqual((await self.client.get('/runs?exclusion_status=all')).json()['total'],1)
        self.assertEqual((await self.client.get('/export.json?include_excluded=true')).json()['runs'][0]['id'],other)
        after=(await self.client.get('/summary')).json()
        self.assertEqual((after['n_total'],after['n_excluded']),(1,0))
        self.assertEqual(len(after['groups']),1)

    async def test_orientation_events_are_exported_without_changing_metrics(self):
        events=[{'seq':i,'event_name':name,'relative_ms':10+i,'details':{'angle':i*90}} for i,name in
            enumerate(['orientation_selected','frame_rotation_changed','preview_rotation_changed'])]
        r=await self.client.post(f'/runs/{self.id}/events',json={'events':events})
        self.assertEqual(r.status_code,200,r.text)
        data=(await self.client.get(f'/runs/{self.id}/export.json')).json()['runs'][0]
        self.assertEqual([e['details']['angle'] for e in data['events']],[0,90,180])
        self.assertFalse(data['excluded_from_analysis'])
