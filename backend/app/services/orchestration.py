"""Persistent admin experiment operations. No synthetic observations."""
import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
import time
from uuid import uuid4

import httpx
from sqlalchemy import text
from app.db.session import AnnotationSessionLocal, CatalogSessionLocal
from app.core.config import settings
from app.services.ocr_service import OcrService
from app.services.orchestration_resources import OCRResourceMonitor
from app.services.composition_cleaner import is_probable_composition_text
from app.services.analysis_service import AnalysisService
from app.schemas.analysis import AnalysisPreferences

log = logging.getLogger(__name__)
live_tasks = set()
PROTOCOL = 'live-v3.2'
SERVER_VERSION = settings.research_app_version

def now():
    return datetime.now(timezone.utc)

def encoded(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)

def sufficient(status, composition):
    return status == 'success' and isinstance(composition, str) and bool(composition.strip()) and is_probable_composition_text(composition)

class MeasuredOCR(OcrService):
    def __init__(self, started_callback=None):
        self.measurements = {}
        self.started_callback = started_callback

    def _log_ocr_result(self, *args, **kwargs):
        # The result belongs in the protected run, never in general text logs.
        pass

    def _recognize_embedded_sync(self, payload, source):
        m = self.measurements
        m['worker_started_at'] = now()
        if self.started_callback:
            self.started_callback(m['worker_started_at'])
        start = time.perf_counter()
        observer = OCRResourceMonitor()
        observer.start()
        try:
            return super()._recognize_embedded_sync(payload, source)
        finally:
            m.update(observer.finish())
            m['worker_finished_at'] = now()
            m['ocr_worker_ms'] = (time.perf_counter() - start) * 1000

async def analyze(composition):
    async with CatalogSessionLocal() as db:
        result = await AnalysisService(db).analyze(composition, AnalysisPreferences())
        return result.model_dump(mode='json', by_alias=True)

async def server_event(db, run_id, name, details):
    # Serialize sequence allocation with browser writes/finalization.
    await db.execute(text('SELECT id FROM orchestration_lab.runs WHERE id=:id FOR UPDATE'), {'id': run_id})
    await db.execute(text("""INSERT INTO orchestration_lab.events(run_id,seq,event_name,source,details)
        SELECT :id,coalesce(max(seq),-1)+1,:name,'server',CAST(:details AS jsonb)
        FROM orchestration_lab.events WHERE run_id=:id AND source='server'"""),
        {'id': run_id, 'name': name, 'details': encoded(details)})

async def perform(task_id, run_id, kind, value, lookup_ms):
    start = time.perf_counter()
    result, metrics, ocr = {}, {}, None
    worker_start, worker_end = None, None
    ok = False
    try:
        if kind == 'ocr':
            loop = asyncio.get_running_loop()
            async def record_start(at):
                async with AnnotationSessionLocal() as db, db.begin():
                    await db.execute(text('SELECT id FROM orchestration_lab.runs WHERE id=:id FOR UPDATE'),dict(id=run_id))
                    await db.execute(text("UPDATE orchestration_lab.ocr_tasks SET worker_started_at=:at WHERE id=:id AND status='running'"),dict(at=at,id=task_id))
                    await server_event(db,run_id,'ocr_worker_started',{'task_id':str(task_id),'at':at})
            def started(at):
                future=asyncio.run_coroutine_threadsafe(record_start(at),loop)
                try:
                    future.result(timeout=5)
                except Exception as exc:
                    log.warning('Worker start persistence unavailable task_id=%s type=%s',task_id,type(exc).__name__)
            ocr = MeasuredOCR(started)
            result_model = await ocr.recognize_image(value, source='orchestration:' + str(task_id))
            result = result_model.model_dump(mode='json', by_alias=True)
            composition = result_model.composition_text
            ok = sufficient(result_model.status, composition)
            metrics['input_bytes'] = len(base64.b64decode(value.split(',')[-1]))
            metrics['technical_response_status'] = result_model.status
        else:
            url = f'https://world.openfoodfacts.org/api/v3.6/product/{value}.json'
            metrics.update(url=url, barcode=value, cache_policy='no_application_cache')
            lookup_start = time.perf_counter()
            async with httpx.AsyncClient(timeout=lookup_ms/1000, follow_redirects=False) as client:
                async with client.stream('GET', url, params={'fields':'code,product_name,ingredients_text'},
                    headers={'User-Agent':'ToxiCheck-Orchestration/3.0 (https://github.com/Elraden/toxicheck_test)',
                             'Cache-Control':'no-cache', 'Accept':'application/json'}) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2*1024*1024:
                            raise ValueError('lookup_response_too_large')
                    metrics.update(http_status=response.status_code, bytes=len(body),
                        retry_after=response.headers.get('retry-after'), age=response.headers.get('age'),
                        lookup_ms=(time.perf_counter()-lookup_start)*1000)
                    data = json.loads(body) if response.status_code == 200 else {}
                    product = data.get('product') or {}
                    composition = product.get('ingredients_text') if isinstance(product, dict) else None
                    same = str(product.get('code', data.get('code',''))) == value if isinstance(product,dict) else False
                    ok = response.status_code == 200 and same and sufficient('success',composition)
                    result = {'status':'success' if ok else 'insufficient', 'product':product,
                              'ingredientsText':composition, 'http_status':response.status_code}
        if ok:
            result['analysis'] = await analyze(composition)
    except Exception as exc:
        ok = False
        result = {'status':'error', 'error_type':type(exc).__name__}
        metrics['error_type'] = type(exc).__name__
        log.warning('Lab task failed run_id=%s task_id=%s kind=%s type=%s',run_id,task_id,kind,type(exc).__name__)
    finally:
        if ocr:
            metrics.update(ocr.measurements)
            worker_start = metrics.pop('worker_started_at', None)
            worker_end = metrics.pop('worker_finished_at', None)
        metrics['server_elapsed_ms'] = (time.perf_counter()-start)*1000
        metrics['client_cpu'] = 'not_measured'
        log.info('Lab worker finished run_id=%s task_id=%s kind=%s sufficient=%s elapsed_ms=%.1f',
                 run_id,task_id,kind,ok,metrics['server_elapsed_ms'])
        async def persist():
            async with AnnotationSessionLocal() as db, db.begin():
                await db.execute(text("SELECT id FROM orchestration_lab.runs WHERE id=:id FOR UPDATE"),dict(id=run_id))
                await db.execute(text("""UPDATE orchestration_lab.ocr_tasks SET
                    status=:status,result=CAST(:result AS jsonb),sufficient=:ok,
                    worker_started_at=:ws,worker_finished_at=:we,metrics=metrics || CAST(:metrics AS jsonb),
                    measurement_quality=:quality,rss_peak_bytes=:rss,
                    cancellation=cancellation || CAST(:cancel AS jsonb)
                    WHERE id=:id"""),
                    dict(id=task_id,status='failed' if result.get('status')=='error' else 'finished',
                         result=encoded(result),ok=ok,ws=worker_start,we=worker_end,metrics=encoded(metrics),
                         quality=metrics.get('measurement_quality','not_measured'),
                         rss=metrics.get('ocr_process_tree_rss_peak_bytes') or metrics.get('shared_process_rss_peak_bytes'),
                         cancel=encoded({'worker_stopped':worker_end is not None,'cancellation_effective':False})))
                await server_event(db, run_id, ('ocr_worker_finished' if worker_end is not None else 'ocr_request_finished') if kind=='ocr' else 'lookup_http_finished',
                    {'task_id':str(task_id),'sufficient_result':ok,**metrics})
        # Retries concern persistence only. Never run OCR/OFF twice on a DB retry.
        for attempt in range(3):
            try:
                await persist()
                break
            except Exception:
                if attempt == 2:
                    log.exception('Lab persistence failed run_id=%s task_id=%s',run_id,task_id)
                else:
                    await asyncio.sleep(.5*(attempt+1))

def launch(*args):
    task = asyncio.create_task(perform(*args))
    live_tasks.add(task)
    task.add_done_callback(live_tasks.discard)

async def recover():
    """Called once at boot in the supported single API worker deployment."""
    async with AnnotationSessionLocal() as db, db.begin():
        if not await db.scalar(text("SELECT to_regclass('orchestration_lab.runs')")):
            return
        await db.execute(text("""UPDATE orchestration_lab.ocr_tasks SET status='interrupted',
            measurement_quality='unknown',metrics=metrics || '{"interruption":"server_restart"}'::jsonb
            WHERE status='running'"""))
        await db.execute(text("""UPDATE orchestration_lab.runs SET status='interrupted',
            outcome='technical_failure',finished_at=now(),
            summary=summary || '{"interruption":"server_restart"}'::jsonb WHERE status='running'"""))

def measured_summary(run, tasks, events):
    """Server facts and labelled browser observations; no cross-clock subtraction."""
    browser=[e for e in events if e.get('source')=='browser']
    first={}
    for event in browser:
        first.setdefault(event['event_name'],event.get('relative_ms'))
    def delta(a,b):
        x,y=first.get(a),first.get(b)
        return max(0,y-x) if isinstance(x,(float,int)) and isinstance(y,(float,int)) else None
    ocr=[t for t in tasks if t['kind']=='ocr']
    lookup=[t for t in tasks if t['kind']=='lookup']
    def complete_sum(key):
        values=[t['metrics'].get(key) for t in ocr]
        return sum(values) if values and all(isinstance(x,(int,float)) for x in values) else None
    peak=[t['metrics'].get('shared_process_rss_peak_bytes') for t in ocr]
    tess_peaks=[t['metrics'].get('tesseract_child_rss_peak_bytes') for t in ocr]
    tess_peaks=[x for x in tess_peaks if isinstance(x,int)]
    tree_peaks=[t['metrics'].get('ocr_process_tree_rss_peak_bytes') for t in ocr]
    tree_peaks=[x for x in tree_peaks if isinstance(x,int)]
    peak=[x for x in peak if isinstance(x,int)]
    winner=run['summary'].get('winning_task_id')
    # The result actually used by the strategy is the winner; for unsuccessful
    # OCR runs report the last attempted frame, never the best retrospectively.
    representative=next((t for t in ocr if str(t['id'])==str(winner)),None)
    if representative is None and ocr and run['outcome']!='valid_barcode':
        representative=ocr[-1]
    review=representative.get('quality_review') if representative else None
    reviewed=review is not None
    capture_processing=[e.get('details',{}).get('capture_ms') for e in browser if e['event_name']=='frame_evaluated']
    # UTF-8 JSON reserialization estimates; network TLS/compression not included.
    traffic=[e['details'] for e in browser if e['event_name']=='task_traffic']
    traffic_complete=len(traffic)==len(tasks) and all(
        isinstance(t.get(k),(int,float)) and t[k]>=0
        for t in traffic for k in ('upload_json_bytes','download_json_bytes'))
    def transfer(kind,key):
        return sum(t[key] for t in traffic if t.get('kind')==kind) if traffic_complete else None
    upload=sum(t['upload_json_bytes'] for t in traffic) if traffic_complete else None
    download=sum(t['download_json_bytes'] for t in traffic) if traffic_complete else None
    return dict(
        sufficient_result=run['valid_result'],final_outcome=run['outcome'],
        end_to_end_ms=first.get('result_displayed') if run['valid_result'] else None,
        camera_setup_ms=first.get('camera_ready'),
        barcode_decode_ms=delta('barcode_started','barcode_detected'),
        lookup_ms=lookup[0]['metrics'].get('lookup_ms') if lookup else None,
        photo_wait_ms=delta('photo_hint_shown','photo_ready'),
        frame_wait_ms=delta('camera_ready','frame_selected'),
        ocr_calls=len(ocr),api_calls=len(tasks),
        frames_considered=sum(e['event_name']=='frame_considered' for e in browser),
        frames_sent=sum(e['event_name']=='frame_selected' for e in browser),
        frames_rejected=sum(e['event_name']=='frame_rejected' for e in browser),
        frame_capture_ms=sum(x for x in capture_processing if isinstance(x,(float,int))) if capture_processing else None,
        client_capture_processing_ms=sum(x for x in capture_processing if isinstance(x,(float,int))) if capture_processing else None,
        client_frame_selection_ms=sum(e['details'].get('selection_ms',0) for e in browser
            if e['event_name']=='frame_selection_work'),
        traffic_measurement_quality='utf8_json_reserialized_estimate' if traffic_complete else 'not_measured',
        ocr_upload_bytes=transfer('ocr','upload_json_bytes'),
        ocr_download_bytes=transfer('ocr','download_json_bytes'),
        lookup_upload_bytes=transfer('lookup','upload_json_bytes'),
        lookup_download_bytes=transfer('lookup','download_json_bytes'),
        total_transfer_bytes=upload+download if traffic_complete else None,
        ocr_image_bytes=sum(t['metrics'].get('input_bytes',0) for t in ocr) if ocr and
            all(isinstance(t['metrics'].get('input_bytes'),int) for t in ocr) else None,
        quality_reviewed=reviewed,
        ingredient_recall=review.get('ingredient_recall') if reviewed else None,
        quality_success=review.get('quality_success') if reviewed else None,
        ocr_worker_ms=complete_sum('ocr_worker_ms'),
        ocr_cpu_time_ms=complete_sum('ocr_cpu_time_ms'),
        ocr_python_thread_cpu_ms=complete_sum('ocr_python_thread_cpu_ms'),
        tesseract_child_cpu_time_ms=complete_sum('tesseract_child_cpu_time_ms'),
        tesseract_child_rss_peak_bytes=max(tess_peaks) if tess_peaks else None,
        ocr_process_tree_rss_peak_bytes=max(tree_peaks) if tree_peaks else None,
        shared_process_cpu_delta_ms=complete_sum('shared_process_cpu_delta_ms'),
        shared_process_rss_peak_bytes=max(peak) if peak else None,
        measurement_quality='sampled_process_tree' if tess_peaks else ('shared_process_estimate' if peak else 'not_measured'),
        cancel_requested=any(t['cancellation'].get('cancel_requested',False) for t in tasks),
        pending_worker=any(t['status']=='running' for t in ocr),
        worker_stopped_after_run=any(run['finished_at'] and t['worker_finished_at'] and
            t['worker_finished_at']>run['finished_at'] for t in ocr),
        redundant_ocr_started=bool(ocr) and run['outcome']=='valid_barcode',
        winner_task_id=winner,client_metrics_quality='browser_observation',
    )

def aggregate(runs):
    import math
    grouped={}
    for run in runs:
        if run.get('excluded_from_analysis',False): continue
        # Environment's run-start wall time isn't an experimental condition.
        env={k:v for k,v in run['environment'].items() if k!='started_at_utc'}
        group_key=encoded([run['protocol_version'],run['mode'],run['config'],env])
        grouped.setdefault(group_key,[]).append(run)
    result=[]
    for key,rows in grouped.items():
        summaries=[r['measured_summary'] for r in rows]
        times=sorted(s['end_to_end_ms'] for s in summaries if isinstance(s['end_to_end_ms'],(int,float)))
        values=[s['shared_process_cpu_delta_ms'] for s in summaries]
        child_cpu=[s.get('ocr_cpu_time_ms') for s in summaries]
        child_complete=all(v is not None or s['ocr_calls']==0 for v,s in zip(child_cpu,summaries))
        child_total=sum(v or 0 for v in child_cpu) if child_complete else None
        transfer=[s.get('total_transfer_bytes') for s in summaries]
        transfer_complete=all(isinstance(v,(float,int)) for v in transfer)
        transfer_total=sum(transfer) if transfer_complete else None
        image_bytes=[s.get('ocr_image_bytes') for s in summaries]
        image_complete=all(isinstance(v,(int,float)) or s['ocr_calls']==0 for v,s in zip(image_bytes,summaries))
        image_total=sum(v or 0 for v in image_bytes) if image_complete else None
        full_scans=sum(s.get('quality_reviewed') and s.get('quality_success') is True for s in summaries)
        complete=all(v is not None or s['ocr_calls']==0 for v,s in zip(values,summaries))
        cpu=sum(v or 0 for v in values) if complete else None
        peaks=[s['shared_process_rss_peak_bytes'] for s in summaries if s['shared_process_rss_peak_bytes'] is not None]
        success=sum(r['valid_result'] for r in rows)
        quality_reviewed=sum(s.get('quality_reviewed',False) for s in summaries)
        quality_successes=sum(s.get('quality_success') is True for s in summaries)
        quality_recalls=[s['ingredient_recall'] for s in summaries if isinstance(s.get('ingredient_recall'),(int,float))]
        result.append(dict(key=hashlib.sha256(key.encode()).hexdigest(),mode=rows[0]['mode'],protocol=rows[0]['protocol_version'],
            config=rows[0]['config'],environment={k:v for k,v in rows[0]['environment'].items() if k!='started_at_utc'},
            n=len(rows),success=success,success_rate=success/len(rows),running=sum(r['status']=='running' for r in rows),
            quality_reviewed=quality_reviewed,quality_successes=quality_successes,
            quality_success_rate=quality_successes/quality_reviewed if quality_reviewed else None,
            mean_ingredient_recall=sum(quality_recalls)/len(quality_recalls) if quality_recalls else None,
            p50=times[max(0,math.ceil(len(times)*.5)-1)] if times else None,
            p95=times[max(0,math.ceil(len(times)*.95)-1)] if times else None,
            timed_successes=len(times),ocr_calls=sum(s['ocr_calls'] for s in summaries),
            ocr_cpu_per_run_ms=child_total/len(rows) if child_total is not None else None,
            ocr_cpu_per_complete_scan_ms=child_total/full_scans if child_total is not None and full_scans else None,
            total_transfer_bytes=transfer_total,
            transfer_per_run_bytes=transfer_total/len(rows) if transfer_total is not None else None,
            bytes_per_complete_scan=transfer_total/full_scans if transfer_total is not None and full_scans else None,
            ocr_image_bytes=image_total,
            api_calls=sum(s['api_calls'] for s in summaries),ocr_cpu_time_ms=child_total,
            shared_process_cpu_per_run_ms=cpu/len(rows) if cpu is not None else None,
            shared_process_cpu_per_success_ms=cpu/success if cpu is not None and success else None,
            shared_process_rss_peak_bytes=max(peaks) if peaks else None,
            tesseract_child_rss_peak_bytes=max((s.get('tesseract_child_rss_peak_bytes') for s in summaries
                if isinstance(s.get('tesseract_child_rss_peak_bytes'),(int,float))), default=None),
            ocr_process_tree_rss_peak_bytes=max((s.get('ocr_process_tree_rss_peak_bytes') for s in summaries
                if isinstance(s.get('ocr_process_tree_rss_peak_bytes'),(int,float))), default=None),
            measurement_quality='sampled_process_tree' if child_complete and child_total is not None else 'not_measured'))
    return result
