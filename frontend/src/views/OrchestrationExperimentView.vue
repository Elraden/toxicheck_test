<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { BrowserMultiFormatReader, type IScannerControls } from '@zxing/browser';
import { BarcodeFormat, DecodeHintType } from '@zxing/library';
import { refreshSession } from '@/services/userSession';
import { Orchestrator, defaults, modes, realClock, validateConfig, type Mode, type Config, type Event, type Task, type Finish } from '@/research/orchestration/engine';
import { capture, resetFrames, photoImage, previewRotation, rotatedSize, quarterTurn, frameScore, type FrameState, type CapturedFrame, type FrameAssessment } from '@/research/orchestration/capture';
import { read, write, download } from '@/research/orchestration/api';
import { apiBaseUrl, ApiError } from '@/services/http';

type Case = {id:string;product_label:string;expected_barcode:string|null;label_layout:string;notes:string;reference_ingredients:string[]};
type QualityReview={matched_indices:number[];matched_count:number;reference_count:number;ingredient_recall:number;quality_success:boolean;frame_complete:boolean|null;note:string};
type QualityDraft={matched_indices:number[];frame_complete:boolean|null;note:string};
type LabTask=Task&{quality_review?:QualityReview|null};
type Run = {environment:Record<string,unknown>;excluded_from_analysis:boolean;exclusion_reason:string|null;exclusion_comment:string|null;exclusion_audit?:{id:number;action:string;reason:string;comment:string|null;created_at:string}[];id:string;case_id:string;mode:Mode;status:string;outcome:string|null;valid_result:boolean;protocol_version:string;config:Config;started_at:string;summary:{client_observations?:Record<string,unknown>};needs_review:boolean;events?:Event[];tasks?:LabTask[];case?:Case;measured_summary?:Record<string,unknown>};
const statuses:Record<string,string>={waiting:'Ожидание',idle:'Не запущен',running:'Выполняется',detected:'Найден',failed:'Не найден',stopped_for_photo:'Переход к фото',timeout:'Тайм-аут',error:'Ошибка',sufficient:'Состав получен',insufficient:'Недостаточно данных',valid_barcode:'Состав по штрихкоду',valid_ocr:'Состав через OCR',incomplete:'Недостаточно данных',cancelled:'Прерван оператором',technical_failure:'Техническая ошибка',finished:'Завершён',interrupted:'Прерван'};
const labels:Record<Mode,string>={'photo_sequential':'Фото · последовательно','photo_adaptive':'Фото · адаптивно','video_sequential':'Видео · последовательно','video_parallel':'Видео · параллельно','video_adaptive':'Видео · адаптивно'};
const authorized=ref(false),loading=ref(true),error=ref(''),saveError=ref(''),busy=ref(false),active=ref(false);
const mode=ref<Mode>('photo_sequential'),config=ref<Config>({...defaults});
const cases=ref<Case[]>([]),selectedCase=ref(''),product=ref(''),expected=ref(''),layout=ref('unknown'),notes=ref(''),device=ref('');
const runLayout=ref<'same_side'|'different_side'|'unknown'|''>('');
const newReference=ref(''),referenceDraft=ref(''),qualityDrafts=ref<Record<string,QualityDraft>>({});
const parseReference=(value:string)=>value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
watch(selectedCase,()=>{
  const current=cases.value.find(c=>c.id===selectedCase.value);
  referenceDraft.value=current?.reference_ingredients?.join('\n')??'';
  runLayout.value='';
});
async function saveReference(){
  const current=cases.value.find(c=>c.id===selectedCase.value);if(!current||active.value||busy.value)return;
  const updated=await write<Case>('/cases/'+current.id,{
    product_label:current.product_label,expected_barcode:current.expected_barcode,
    label_layout:current.label_layout,notes:current.notes,reference_ingredients:parseReference(referenceDraft.value)
  },'PUT');
  cases.value=cases.value.map(c=>c.id===updated.id?updated:c);
}

const cameraPanel=ref<HTMLElement>();
const tabs=[{id:'settings',label:'Настройки'},{id:'camera',label:'Камера'},{id:'history',label:'История'}] as const;
const tab=ref<'settings'|'camera'|'history'>('settings');
const orientation=ref<'auto'|'portrait'|'landscape'>('auto'),deviceLandscape=ref(false),angle=ref(0);
const cameraLayout=computed(()=>orientation.value==='auto'?(deviceLandscape.value?'landscape':'portrait'):orientation.value);
const cameraSize=ref({width:1080,height:1920});
const effectiveRotation=computed(()=>previewRotation(cameraSize.value.width,cameraSize.value.height,cameraLayout.value,angle.value));
const previewDimensions=computed(()=>rotatedSize(cameraSize.value.width,cameraSize.value.height,effectiveRotation.value));
const previewRatio=computed(()=>previewDimensions.value.width/previewDimensions.value.height);
const frameStyle=computed(()=>({'--frame-ratio':String(previewRatio.value)}));
const videoStyle=computed(()=>({
  width:effectiveRotation.value%180?'100cqh':'100%',
  height:effectiveRotation.value%180?'100cqw':'100%',
  transform:`translate(-50%, -50%) rotate(${effectiveRotation.value}deg)`
}));
let originalPhoto:HTMLCanvasElement|null=null;
function syncCameraSize(){
  if(video.value?.videoWidth&&video.value?.videoHeight){
    cameraSize.value={width:video.value.videoWidth,height:video.value.videoHeight};
  }
}
function updatePhotoRotation(){
  if(preview.value&&originalPhoto)preview.value=photoImage(originalPhoto,effectiveRotation.value);
}
function orientationChanged(source='selection'){
  resetFrames(frameState);resetPool();updatePhotoRotation();
  if(active.value)engine?.event('orientation_selected',{orientation:orientation.value,layout:cameraLayout.value,angle:angle.value,effective_angle:effectiveRotation.value,source});
}
function deviceRotated(){
  const next=window.matchMedia('(orientation: landscape)').matches;
  if(next===deviceLandscape.value)return;
  deviceLandscape.value=next;orientationChanged('device');
}
function rotateFrame(){
  angle.value=quarterTurn(angle.value+90);resetFrames(frameState);resetPool();updatePhotoRotation();
  if(active.value)engine?.event('frame_rotation_changed',{angle:angle.value,effective_angle:effectiveRotation.value});
  if(preview.value&&originalPhoto&&active.value)engine?.event('preview_rotation_changed',{angle:angle.value,effective_angle:effectiveRotation.value});
}
function discardPhoto(){preview.value='';originalPhoto=null;photoMeta={};}
const exclusionStatus=ref('active'),includeExcluded=ref(false),nTotal=ref(0),nExcluded=ref(0);
const exclusionDialog=ref<HTMLDialogElement>(),exclusionTarget=ref<Run|null>(null),exclusionReason=ref(''),exclusionComment=ref(''),exclusionBusy=ref(false),exclusionError=ref('');
const reasons:Record<string,string>={wrong_product:'Выбран другой товар',bad_setup:'Неверно подготовлены условия',operator_mistake:'Ошибка оператора',duplicate_trial:'Случайный повтор запуска',instrumentation_error:'Повреждена запись или телеметрия',other:'Другая документированная причина'};
function askExclusion(run:Run){
  exclusionTarget.value=run;exclusionReason.value='';exclusionComment.value='';exclusionError.value='';
  exclusionDialog.value?.showModal();
}
async function saveExclusion(){
  const run=exclusionTarget.value;if(!run||exclusionBusy.value)return;
  exclusionBusy.value=true;exclusionError.value='';
  try{
    await write('/runs/'+run.id+'/exclusion',run.excluded_from_analysis?{excluded:false}:
      {excluded:true,reason:exclusionReason.value,comment:exclusionComment.value.trim()||null},'PUT');
    await loadHistory();
    if(detail.value?.id===run.id)detail.value=await read<Run>('/runs/'+run.id);
    exclusionDialog.value?.close();
  }catch(e){exclusionError.value=message(e);}finally{exclusionBusy.value=false;}
}
const deletionDialog=ref<HTMLDialogElement>(),deletionTarget=ref<Run|null>(null);
const deletionConfirmation=ref(''),deletionBusy=ref(false),deletionError=ref('');
function askDeletion(run:Run){
  deletionTarget.value=run;deletionConfirmation.value='';deletionError.value='';
  deletionDialog.value?.showModal();
}
async function confirmDeletion(){
  const run=deletionTarget.value;
  if(!run||deletionBusy.value||deletionConfirmation.value!=='УДАЛИТЬ')return;
  deletionBusy.value=true;deletionError.value='';
  try{
    await write('/runs/'+run.id,{},'DELETE');
    if(detail.value?.id===run.id)detail.value=null;
    if(runId.value===run.id){
      runId.value='';outbox=[];pendingFinish=undefined;trace.value=[];result.value=null;outcome.value='';
      sessionStorage.removeItem(key);
    }
    deletionDialog.value?.close();deletionTarget.value=null;
    await loadHistory();
  }catch(e){deletionError.value=message(e);}finally{deletionBusy.value=false;}
}
function changeTab(id:typeof tab.value){if((active.value||busy.value)&&id!=='camera')return;tab.value=id;}
function tabKey(event:KeyboardEvent,index:number){
  if(active.value||busy.value)return;
  if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;
  event.preventDefault();
  const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  const target=tabs[next];if(target){changeTab(target.id);document.getElementById('lab-tab-'+target.id)?.focus();}
}

const video=ref<HTMLVideoElement>(),preview=ref(''),runId=ref(''),trace=ref<Event[]>([]),version=ref(0);
const result=ref<Task|null>(null),outcome=ref(''),history=ref<Run[]>([]),total=ref(0),offset=ref(0),detail=ref<Run|null>(null);
const filterMode=ref(''),filterCase=ref(''),filterOutcome=ref(''),since=ref(''),until=ref('');
let engine:Orchestrator|undefined,stream:MediaStream|undefined,controls:IScannerControls|undefined;
let frameTimer:ReturnType<typeof setInterval>|undefined,flushTimer:ReturnType<typeof setInterval>|undefined;
let frameState:FrameState={previous:null,lastSent:null},outbox:Event[]=[],flushing=false,pendingFinish:Finish|undefined;
const orphanedPending=ref(false);
let framePool:CapturedFrame[]=[],poolStarted=0,photoMeta:Record<string,unknown>={};
function resetPool(){framePool=[];poolStarted=0;}
function observeFrame(q:FrameAssessment){
  engine?.event('frame_evaluated',q as unknown as Record<string,unknown>);
  if(!q.accepted)engine?.event('frame_rejected',{reason:q.reason,sharpness:q.sharpness,brightness:q.brightness});
}
function toggleQuality(taskId:string,index:number,enabled:boolean){
  const draft=qualityDrafts.value[taskId];if(!draft)return;
  draft.matched_indices=enabled?[...draft.matched_indices.filter(v=>v!==index),index]:draft.matched_indices.filter(v=>v!==index);
}
async function saveQuality(taskId:string){
  const run=detail.value,draft=qualityDrafts.value[taskId];if(!run||!draft)return;
  await write('/runs/'+run.id+'/tasks/'+taskId+'/quality',draft,'PUT');
  await openRun(run.id);
}
function frameImage(runId:string,taskId:string){
  return apiBaseUrl+'/admin/orchestration/runs/'+runId+'/tasks/'+taskId+'/image';
}

let destroyed=false,generation=0;
const tasks=new Map<string,boolean>();
const taskKinds=new Map<string,string>();
const state=computed(()=>{void version.value;return engine;});
const detailReference=computed(()=>Array.isArray(detail.value?.environment?.reference_ingredients)?detail.value.environment.reference_ingredients as string[]:[]);
const isVideo=computed(()=>mode.value.startsWith('video'));
const videoInstruction='Покажите камере штрихкод и текст состава. Если они расположены на разных сторонах упаковки, поверните товар и наведите камеру на состав. Снимок делается автоматически.';
const key='toxicheck-orchestration-pending-v3';
function message(e:unknown){return e instanceof Error?e.message:String(e);}
function stash(){
  try{
    // A completed, fully synchronized run has no recovery queue. Never leave
    // its ID in sessionStorage: it may later be deleted on the server.
    if(!outbox.length&&!pendingFinish&&!active.value)sessionStorage.removeItem(key);
    else sessionStorage.setItem(key,JSON.stringify({id:runId.value,events:outbox,finish:pendingFinish}));
  }catch{saveError.value='Не удалось сохранить локальную очередь. Не закрывайте страницу до синхронизации.';}
}
function pendingMissing(e:unknown){
  if(e instanceof ApiError&&e.status===404){
    orphanedPending.value=true;
    saveError.value='Сервер не находит запуск из локальной очереди. Войдите под прежним администратором или сохраните резервную копию очереди и очистите её вручную.';
    return true;
  }
  return false;
}
function backupPending(){
  download({run_id:runId.value,events:outbox,finish:pendingFinish??null,reason:'recovery_backup'},
    'toxicheck-pending-'+runId.value+'.json');
}
function discardOrphaned(){
  if(!orphanedPending.value||active.value)return;
  if(!window.confirm('Удалить только локальную очередь этого браузера? Записи на сервере и история экспериментов не удаляются. Сначала сохраните JSON, если события ещё нужны.'))return;
  sessionStorage.removeItem(key);
  runId.value='';outbox=[];pendingFinish=undefined;orphanedPending.value=false;saveError.value='';
}
async function retryPending(){
  orphanedPending.value=false;saveError.value='';
  try{
    await read<Run>('/runs/'+runId.value);
    await flush();
  }catch(e){
    if(!pendingMissing(e))saveError.value='Данные ещё не синхронизированы: '+message(e);
  }
}
async function flush(){
  if(flushing||orphanedPending.value||!runId.value||(!outbox.length&&!pendingFinish))return;
  flushing=true;
  try{
    if(outbox.length){
      const batch=outbox.slice(0,100);
      await write('/runs/'+runId.value+'/events',{events:batch});
      outbox.splice(0,batch.length);stash();
    }
    if(!outbox.length&&pendingFinish){
      await write('/runs/'+runId.value+'/finish',pendingFinish);
      pendingFinish=undefined;stash();
      await loadHistory();
    }
    if(!outbox.length&&!pendingFinish)stash();
    saveError.value='';
  }catch(e){
    if(!pendingMissing(e))saveError.value='Данные ещё не синхронизированы: '+message(e);
  }finally{flushing=false;}
}
function emit(e:Event){trace.value.push(e);outbox.push(e);stash();}
function stopBarcode(){controls?.stop();controls=undefined;}
function stopCamera(){stopBarcode();stream?.getTracks().forEach(t=>t.stop());stream=undefined;if(frameTimer)clearInterval(frameTimer);resetPool();}
function cancelTasks(kind?:string){
  for(const [id,running] of tasks)if(running&&(!kind||taskKinds.get(id)===kind))void write('/runs/'+runId.value+'/tasks/'+id+'/cancel',{}).catch(e=>{saveError.value=message(e);});
}
function jsonBytes(value:unknown):number{
  // Client-side UTF-8 JSON payload estimate, not compressed wire-level traffic.
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}
async function task(kind:'lookup'|'ocr',value:string,captureMetrics:Record<string,unknown>={}):Promise<Task>{
  const id=crypto.randomUUID(),current=runId.value,start=performance.now();
  tasks.set(id,true);taskKinds.set(id,kind);
  let row:Task;
  const payload={id,...(kind==='ocr'?{image_base64:value,capture_metrics:captureMetrics}:{barcode:value})};
  const uploadJsonBytes=jsonBytes(payload);
  let downloadJsonBytes=0,polls=0,responses=0,acknowledged=false;
  try{
    row=await write<Task>('/runs/'+current+'/'+kind,payload);
    acknowledged=true;downloadJsonBytes+=jsonBytes(row);responses++;
    // Durable task polling is separate from HTTP acknowledgement of the submission.
    engine?.event(kind==='ocr'?'ocr_submission_ack':'lookup_submission_ack',{task_id:id,http_status:200,submission_ms:performance.now()-start});
    for(let n=0;row.status==='running'&&n<240;n++){
      await new Promise(resolve=>setTimeout(resolve,500));
      row=await read<Task>('/runs/'+current+'/tasks/'+id);
      downloadJsonBytes+=jsonBytes(row);responses++;polls++;
      if(destroyed)throw new Error('Страница закрыта; серверная задача сохраняет результат самостоятельно.');
    }
    if(row.status==='running')throw new Error('Серверная задача ещё работает. Проверьте историю.');
    if(current===runId.value)engine?.event(kind==='ocr'?'ocr_worker_observed':'lookup_response_observed',{
      task_id:id,http_ms:performance.now()-start,metrics:row.metrics});
    return row;
  }finally{
    if(current===runId.value)engine?.event('task_traffic',{
      task_id:id,kind,upload_json_bytes:uploadJsonBytes,download_json_bytes:downloadJsonBytes,
      response_count:responses,poll_requests:polls,submission_acknowledged:acknowledged,
      measurement_quality:'utf8_json_reserialized_estimate'
    });
    tasks.set(id,false);
  }
}
async function finished(value:Finish,taskResult?:Task){
  active.value=false;outcome.value=value.outcome;result.value=taskResult??null;
  stopCamera();
  await nextTick();
  if(taskResult){
    engine?.event('result_displayed');
    value.summary.end_to_end_ms=performance.now()-(engine?.started??performance.now());
  }else value.summary.end_to_end_ms=null;
  value.summary.events_clock='browser_performance_now';
  pendingFinish=value;stash();await flush();
}
async function start(){
  if(busy.value||active.value)return;
  error.value='';busy.value=true;result.value=null;outcome.value='';
  let acquired:MediaStream|undefined;
  const started=performance.now(),token=++generation;
  try{
    validateConfig(config.value);
    if(!selectedCase.value)throw new Error('Выберите или добавьте товар.');
    if(!runLayout.value)throw new Error('Перед началом укажите расположение штрихкода и состава.');
    if(outbox.length||pendingFinish||orphanedPending.value)throw new Error('Сначала восстановите или сохраните и очистите локальную очередь предыдущего запуска.');
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('Нужны HTTPS и браузер с доступом к камере.');
    runId.value=crypto.randomUUID();trace.value=[];tasks.clear();frameState={previous:null,lastSent:null};resetPool();discardPhoto();tab.value='camera';
    // Request permission in the user-gesture flow, but persist run even if permission fails.
    const cameraPromise=navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}})
      .then(s=>{acquired=s;return {stream:s,error:null};}).catch(e=>({stream:null,error:e}));
    try{
      await write('/runs',{id:runId.value,case_id:selectedCase.value,mode:mode.value,config:config.value,
        environment:{user_agent:navigator.userAgent,device_label:device.value,started_at_utc:new Date().toISOString(),zxing_browser_version:'0.2.1',
          initial_orientation:orientation.value,initial_layout:cameraLayout.value,initial_frame_rotation:angle.value,
          label_layout:runLayout.value,layout_confirmed_before_run:true,capture_policy:'best_of_three_v1'}});
    }catch(e){void cameraPromise.then(r=>r.stream?.getTracks().forEach(t=>t.stop()));throw e;}
    engine=new Orchestrator(mode.value,config.value,{
      clock:realClock,emit,lookup:code=>task('lookup',code),ocr:(image,metrics)=>task('ocr',image,metrics),cancel:()=>cancelTasks(),cancelLookup:()=>cancelTasks('lookup'),
      stopBarcode,changed:()=>{version.value++;},finished:(v,t)=>{void finished(v,t);}
    },started);
    active.value=true;engine.start();stash();
    const ready=await cameraPromise;
    if(token!==generation||destroyed||engine.done){ready.stream?.getTracks().forEach(t=>t.stop());return;}
    if(ready.error||!ready.stream)throw new Error('Камера недоступна: '+message(ready.error));
    stream=ready.stream;
    await nextTick();
    if(!video.value)throw new Error('Не удалось открыть просмотр камеры.');
    video.value.srcObject=stream;await video.value.play();syncCameraSize();engine.ready();
    const settings=stream.getVideoTracks()[0]?.getSettings();
    engine.event('camera_settings',{width:settings?.width,height:settings?.height,frame_rate:settings?.frameRate,facing_mode:settings?.facingMode});
    cameraPanel.value?.scrollIntoView({block:'center',behavior:'smooth'});
    const hints=new Map();hints.set(DecodeHintType.POSSIBLE_FORMATS,[BarcodeFormat.EAN_13,BarcodeFormat.EAN_8,BarcodeFormat.UPC_A,BarcodeFormat.UPC_E]);
    const reader=new BrowserMultiFormatReader(hints,{delayBetweenScanAttempts:200,delayBetweenScanSuccess:500});
    controls=await reader.decodeFromVideoElement(video.value,(decoded,_error,ctrl)=>{
      if(engine?.done||token!==generation){ctrl.stop();return;}
      if(decoded){engine?.detected(decoded.getText());ctrl.stop();}
    });
    if(engine.done||engine.barcodeState!=='running')stopBarcode();
    frameTimer=setInterval(()=>{
      if(!engine||engine.done||!isVideo.value||!video.value){resetPool();return;}
      if(!engine.canCapture()){resetPool();return;}
      if(!poolStarted)poolStarted=performance.now();
      engine.event('frame_considered');
      const frame=capture(video.value,frameState,true,effectiveRotation.value,observeFrame);
      if(frame)framePool.push(frame);
      if(framePool.length>=3||performance.now()-poolStarted>=750){
        if(framePool.length){
          const selectStarted=performance.now();
          const best=framePool.reduce((a,b)=>frameScore(b.quality)>frameScore(a.quality)?b:a);
          engine.event('frame_selection_work',{selection_ms:performance.now()-selectStarted,candidates:framePool.length});
          const metrics={...best.quality,candidates_considered:framePool.length,
            selection_score:frameScore(best.quality),selection_policy:'best_of_three_v1'};
          if(engine.submit(best.image,false,metrics))frameState.lastSent=best.fingerprint;
        }
        resetPool();
      }
    },250);
  }catch(e){
    error.value=message(e);
    if(engine&&!engine.done)engine.finish('technical_failure');
    acquired?.getTracks().forEach(t=>t.stop());
  }finally{busy.value=false;}
}
function takePhoto(){
  if(!engine||!video.value)return;
  engine.selectPhoto();
  const frame=capture(video.value,frameState,false,effectiveRotation.value,observeFrame);
  if(frame){originalPhoto=frame.original;preview.value=frame.image;
    photoMeta={...frame.quality,candidates_considered:1,selection_policy:'manual_photo'};
  }else error.value='Дождитесь изображения камеры и попробуйте снова.';
}
function confirmPhoto(){if(preview.value&&engine?.submit(preview.value,true,photoMeta))discardPhoto();}
async function addCase(){
  error.value='';try{
    const item=await write<Case>('/cases',{product_label:product.value,expected_barcode:expected.value||null,label_layout:layout.value,notes:notes.value,reference_ingredients:parseReference(newReference.value)});
    cases.value.unshift(item);selectedCase.value=item.id;referenceDraft.value=item.reference_ingredients.join('\n');newReference.value='';
  }catch(e){error.value=message(e);}
}
function query(){
  const q=new URLSearchParams();
  if(filterMode.value)q.set('mode',filterMode.value);
  if(filterCase.value)q.set('case_id',filterCase.value);
  if(filterOutcome.value)q.set('outcome',filterOutcome.value);
  if(since.value)q.set('since',new Date(since.value).toISOString());
  if(until.value)q.set('until',new Date(until.value).toISOString());
  return q;
}
async function loadHistory():Promise<void>{
  const q=query();q.set('offset',String(offset.value));q.set('exclusion_status',exclusionStatus.value);
  const data=await read<{items:Run[];total:number}>('/runs?'+q);
  if(offset.value>0&&offset.value>=data.total){offset.value=Math.max(0,Math.floor((data.total-1)/30)*30);return loadHistory();}
  history.value=data.items;total.value=data.total;
  const summary=await read<{groups:Aggregate[];n_total:number;n_excluded:number}>('/summary?'+query());
  summaries.value=summary.groups;nTotal.value=summary.n_total;nExcluded.value=summary.n_excluded;
}
async function safely(fn:()=>Promise<unknown>){try{error.value='';await fn();}catch(e){error.value=message(e);}}
async function openRun(id:string){
  detail.value=await read<Run>('/runs/'+id);
  qualityDrafts.value=Object.fromEntries((detail.value.tasks??[]).filter(t=>t.kind==='ocr').map(t=>[
    t.id,{matched_indices:[...(t.quality_review?.matched_indices??[])],
      frame_complete:t.quality_review?.frame_complete??null,note:t.quality_review?.note??''}
  ]));
  tab.value='history';await nextTick();document.getElementById('run-detail')?.scrollIntoView({block:'start',behavior:'smooth'});}
async function exportData(id?:string,excluded=false){
  const q=query();q.set('include_excluded',String(id?excluded:includeExcluded.value));
  const data=await read(id?'/runs/'+id+'/export.json?'+q:'/export.json?'+q);
  download(data,'toxicheck-orchestration-'+(id||'series')+'.json');
}
function ms(value:unknown){return typeof value==='number'&&Number.isFinite(value)?(value/1000).toFixed(2)+' с':'Не измерено';}
function cpu(value:unknown){return typeof value==='number'&&Number.isFinite(value)?value.toFixed(0)+' мс':'Не измерено';}
function sizeKb(value:unknown){return typeof value==='number'&&Number.isFinite(value)?(value/1024).toFixed(1)+' КиБ':'Не измерено';}
function sizeMb(value:unknown){return typeof value==='number'&&Number.isFinite(value)?(value/1048576).toFixed(1)+' МиБ':'Не измерено';}
type Aggregate={key:string;mode:Mode;protocol:string;n:number;success:number;p50:number|null;p95:number|null;ocr_calls:number;api_calls:number;shared_process_cpu_per_run_ms:number|null;shared_process_cpu_per_success_ms:number|null;shared_process_rss_peak_bytes:number|null;quality_reviewed:number;quality_successes:number;quality_success_rate:number|null;mean_ingredient_recall:number|null;ocr_cpu_per_run_ms:number|null;ocr_cpu_per_complete_scan_ms:number|null;transfer_per_run_bytes:number|null;bytes_per_complete_scan:number|null;tesseract_child_rss_peak_bytes:number|null;ocr_process_tree_rss_peak_bytes:number|null;frame_reviewed:number;complete_frames:number;frame_completeness_rate:number|null};
const summaries=ref<Aggregate[]>([]);
const timings=computed(()=>{
  const events=detail.value?.events??[];
  const find=(name:string)=>events.find(e=>e.event_name===name)?.relative_ms;
  const delta=(a:string,b:string)=>{const x=find(a),y=find(b);return typeof x==='number'&&typeof y==='number'?y-x:null;};
  return [{label:'Камера',value:delta('run_started','camera_ready')},{label:'Штрихкод',value:delta('barcode_started','barcode_detected')},
    {label:'Lookup (ожидание клиента)',value:delta('lookup_started','lookup_response_observed')},
    {label:'Ожидание фото',value:delta('photo_hint_shown','photo_ready')},
    {label:'Первый кадр после готовности камеры',value:delta('camera_ready','frame_selected')},
    {label:'E2E до результата',value:detail.value?.summary.client_observations?.end_to_end_ms}];
});
function visibility(){if(engine&&!engine.done)engine.event('visibility_changed',{state:document.visibilityState});}
onMounted(async()=>{
  deviceRotated();window.addEventListener('resize',deviceRotated);
  document.addEventListener('visibilitychange',visibility);
  try{
    authorized.value=!!(await refreshSession()).admin;
    if(!authorized.value)return;
    cases.value=await read<Case[]>('/cases?limit=100');
    selectedCase.value=cases.value[0]?.id??'';
    const saved=sessionStorage.getItem(key);
    if(saved){
      try{
        const pending=JSON.parse(saved);
        const events=Array.isArray(pending?.events)?pending.events:[];
        if(!pending?.id||(!events.length&&!pending?.finish)){
          sessionStorage.removeItem(key);
        }else{
          runId.value=pending.id;outbox=events;pendingFinish=pending.finish??undefined;
          try{
            const previous=await read<Run>('/runs/'+runId.value);
            if(previous.status==='running'&&!pendingFinish){
              pendingFinish={outcome:'cancelled',winning_task_id:null,summary:{interruption:'page_reloaded',end_to_end_ms:null}};
              stash();
            }
            await flush();
          }catch(e){
            if(!pendingMissing(e))saveError.value='Не удалось восстановить очередь: '+message(e);
          }
        }
      }catch(e){
        saveError.value='Не удалось прочитать локальную очередь. Не удаляйте данные браузера: '+message(e);
      }
    }
    await loadHistory();
    flushTimer=setInterval(()=>{if(runId.value&&!orphanedPending.value&&(outbox.length||pendingFinish))void flush();},2000);
  }catch(e){error.value=message(e);}finally{loading.value=false;}
});
onBeforeUnmount(()=>{
  window.removeEventListener('resize',deviceRotated);originalPhoto=null;
  document.removeEventListener('visibilitychange',visibility);
  destroyed=true;generation++;if(engine&&!engine.done)engine.finish('cancelled');stopCamera();
  if(flushTimer)clearInterval(flushTimer);
});
</script>

<template>
<section class="lab">
  <header><RouterLink :to="{name:'admin'}">← Админ-режим</RouterLink><h1>Экспериментальный стенд</h1>
    <p>Реальная камера · Open Food Facts · OCR ToxiCheck</p></header>
  <p v-if="loading">Проверяем доступ…</p>
  <p v-else-if="!authorized">Требуется <RouterLink :to="{name:'admin'}">вход администратора</RouterLink>.</p>
  <template v-else>
    <p class="notice">Пилот v3.2: пригодность ответа не означает подтверждённую точность состава. Качество изображения оценивайте отдельно от OCR.</p>
    <div v-if="saveError" class="error" role="alert">
      <p>{{ saveError }}</p>
      <template v-if="orphanedPending">
        <p>В локальной очереди: {{outbox.length}} событий{{pendingFinish?' и завершение запуска':''}}. Их можно сохранить в JSON перед очисткой. История на сервере не удаляется.</p>
        <div class="actions"><button class="secondary" @click="backupPending">Сохранить резервный JSON</button><button class="secondary" @click="retryPending">Проверить повторно</button><button @click="discardOrphaned">Очистить локальную очередь</button></div>
      </template>
      <button v-else @click="flush">Повторить сохранение</button>
    </div>
    <nav class="tabs" role="tablist" aria-label="Разделы стенда">
      <button v-for="(t,i) in tabs" :id="'lab-tab-'+t.id" :key="t.id" :disabled="(active||busy)&&t.id!=='camera'" role="tab" :aria-controls="'lab-panel-'+t.id" :aria-selected="tab===t.id" :tabindex="tab===t.id?0:-1" @click="changeTab(t.id)" @keydown="tabKey($event,i)">{{t.label}}<span v-if="t.id==='camera'&&active"> ●</span></button>
    </nav>
    <section v-show="tab==='settings'" id="lab-panel-settings" role="tabpanel" aria-labelledby="lab-tab-settings">
    <fieldset :disabled="active||busy"><legend>Товар и условия</legend>
      <label>Товар<select v-model="selectedCase"><option value="">Выберите товар</option><option v-for="c in cases" :key="c.id" :value="c.id">{{ c.product_label||c.id }}</option></select></label>
      <p v-if="selectedCase" class="small">case_id: {{ selectedCase }}</p>
      <details><summary>Добавить товар</summary>
        <label>Название<input v-model="product" maxlength="200"></label>
        <label>Ожидаемый штрихкод — только для сверки<input v-model="expected" inputmode="numeric" maxlength="14"></label>
        <label>Штрихкод и состав<select v-model="layout"><option value="unknown">Не указано</option><option value="same_side">На одной стороне</option><option value="different_side">На разных сторонах</option></select></label>
        <label>Заметки<textarea v-model="notes" maxlength="2000"/></label>
        <label>Эталонный состав — по одному ингредиенту на строку<textarea v-model="newReference" rows="5" placeholder="Молоко&#10;Соль&#10;Хлорид кальция"/></label>
        <button @click="addCase">Сохранить товар</button>
      </details>
      <details v-if="selectedCase"><summary>Эталонный состав выбранного товара</summary>
        <p class="small">Укажите отдельные ингредиенты по строкам. После сканирования отметьте те, которые действительно читаются в сыром OCR-тексте.</p>
        <label>Ингредиенты<textarea v-model="referenceDraft" rows="6" placeholder="Каждый ингредиент на отдельной строке"/></label>
        <button type="button" class="secondary" @click="safely(saveReference)">Сохранить эталон</button>
      </details>
      <label>Название устройства<input v-model="device" placeholder="Например: Pixel 8, Chrome" maxlength="150"></label>
      <label>Режим<select v-model="mode"><option v-for="m in modes" :key="m" :value="m">{{ labels[m] }}</option></select></label>
      <details><summary>Настройки пилота, мс</summary>
        <label>Поиск штрихкода<input v-model.number="config.barcodeMaxMs" type="number" min="500" max="15000"></label>
        <label>Подсказка фото<input v-model.number="config.photoHintMs" type="number" min="0" max="14000"></label>
        <label>Ранний OCR<input v-model.number="config.ocrHedgeMs" type="number" min="0" max="14000"></label>
        <label>Ожидание lookup<input v-model.number="config.lookupMaxMs" type="number" min="500" max="15000"></label>
        <label>Весь запуск<input v-model.number="config.runMaxMs" type="number" min="10000" max="120000"></label>
        <label>Интервал кадров<input v-model.number="config.frameMinIntervalMs" type="number" min="1000" max="10000"></label>
        <label>Максимум OCR-запросов<input v-model.number="config.maxOcrRequests" type="number" min="1" max="3"></label>
        <p>Один активный OCR. Профиль сохраняется в каждом запуске. Пороговые значения пока экспериментальные.</p>
      </details>
    </fieldset>
    <button @click="changeTab('camera')">Перейти к камере →</button>
    </section>
    <section v-show="tab==='camera'" id="lab-panel-camera" role="tabpanel" aria-labelledby="lab-tab-camera">
    <p v-if="active" class="small">Во время запуска вкладка камеры остаётся открытой, чтобы браузер не приостанавливал скрытое видео.</p>
    <div class="preflight">
      <label>Тестируемый товар
        <select v-model="selectedCase" :disabled="active||busy">
          <option value="">Выберите товар</option>
          <option v-for="c in cases" :key="c.id" :value="c.id">{{c.product_label||c.id}}</option>
        </select>
      </label>
      <fieldset :disabled="active||busy" class="layout-question">
        <legend>Где находятся штрихкод и состав?</legend>
        <label><input v-model="runLayout" type="radio" value="same_side"/> На одной стороне упаковки</label>
        <label><input v-model="runLayout" type="radio" value="different_side"/> На разных сторонах упаковки</label>
        <label><input v-model="runLayout" type="radio" value="unknown"/> Не удалось определить</label>
      </fieldset>
      <p v-if="runLayout==='different_side'" class="small">Отметьте «Повернул упаковку» при повороте товара. Время поворота будет учтено в событиях.</p>
      <p v-if="runLayout==='different_side'&&mode==='video_parallel'" class="small">Video Parallel возможна, но может отправлять OCR до появления состава. Этот риск измеряем, а не запрещаем стратегию.</p>
      <p class="small">{{labels[mode]}} · эталонных ингредиентов: {{cases.find(c=>c.id===selectedCase)?.reference_ingredients?.length??0}}</p>
    </div>
    <p class="instruction">{{ isVideo?videoInstruction:'Покажите штрихкод. Если данных недостаточно, понадобится фотография состава. При разных сторонах упаковки поверните товар к составу.' }}</p>
    <label>Ориентация области камеры<select v-model="orientation" :disabled="busy" @change="orientationChanged()"><option value="auto">По положению устройства</option><option value="portrait">Вертикальная</option><option value="landscape">Горизонтальная</option></select></label>
    <p class="small">Поворачивайте телефон, чтобы длинный состав поместился в кадр. Кнопка ниже сразу поворачивает живую картинку и одновременно кадр для OCR.</p>
    <div ref="cameraPanel" class="camera-frame" :style="frameStyle">
      <video ref="video" :style="videoStyle" muted playsinline autoplay aria-label="Камера эксперимента" @resize="syncCameraSize"/>
      <img v-if="preview" class="photo-preview" :src="preview" alt="Подтверждение фотографии состава">
      <div v-if="active && (preview || state?.photoHint)" class="camera-controls">
        <template v-if="preview">
          <button :disabled="!state?.canCapture()" @click="confirmPhoto">Подтвердить фото</button>
          <button class="secondary" @click="discardPhoto">Переснять</button>
        </template>
        <button v-else class="capture-button" :disabled="!state?.camera||state?.ocrState==='running'" @click="takePhoto">Сфотографировать состав</button>
      </div>
    </div>
    <button class="secondary rotate" :disabled="busy" @click="rotateFrame">Повернуть {{preview?'превью':'кадр'}} ↻ 90°</button>
    <p class="small">Предпросмотр и изображение, отправляемое в OCR, имеют одинаковую ориентацию. Поворот изображения не увеличивает поле зрения объектива.</p>
    <p v-if="state" role="status">Штрихкод: {{ statuses[state.barcodeState]||state.barcodeState }} · lookup: {{ statuses[state.lookupState]||state.lookupState }} · OCR: {{ statuses[state.ocrState]||state.ocrState }}</p>
    <button v-if="!active" :disabled="busy||!!saveError" @click="start">{{ busy?'Подготовка…':'Начать эксперимент' }}</button>
    <template v-if="active">
      <div class="actions"><button class="secondary" @click="engine?.event('composition_visible')">Состав появился в кадре</button><button class="secondary" @click="engine?.event('package_turned')">Повернул упаковку</button></div>
      <button class="secondary" @click="engine?.finish('cancelled')">Остановить запуск</button>
    </template>
    <article v-if="outcome"><h2>Результат: {{ statuses[outcome]||outcome }}</h2><p v-if="result?.sufficient">Получен пригодный состав</p>
      <p>{{ result?.result?.ingredientsText || result?.result?.compositionText || 'Пригодный состав не получен.' }}</p>
      <p class="small">Оставшаяся серверная работа учитывается отдельно. Обновите подробности запуска после её завершения.</p>
      <button @click="safely(()=>openRun(runId))">Подробности запуска</button>
    </article>
    <details v-if="trace.length"><summary>Хронология текущего запуска ({{trace.length}})</summary><ol class="events"><li v-for="e in trace" :key="e.seq">{{ ms(e.relative_ms) }} · {{ e.event_name }}</li></ol></details>
    </section>
    <section v-show="tab==='history'" id="lab-panel-history" role="tabpanel" aria-labelledby="lab-tab-history">
    <h2>История и выгрузка</h2>
    <details><summary>Фильтры и экспорт</summary>
    <div class="filters">
      <label>Режим<select v-model="filterMode"><option value="">Все</option><option v-for="m in modes" :key="m" :value="m">{{labels[m]}}</option></select></label>
      <label>Товар<select v-model="filterCase"><option value="">Все</option><option v-for="c in cases" :key="c.id" :value="c.id">{{c.product_label||c.id}}</option></select></label>
      <label>Результат<select v-model="filterOutcome"><option value="">Все</option><option v-for="o in ['valid_barcode','valid_ocr','incomplete','timeout','cancelled','technical_failure']" :key="o" :value="o">{{statuses[o]||o}}</option></select></label>
      <label>Показать запуски<select v-model="exclusionStatus"><option value="active">В анализе</option><option value="excluded">Исключённые</option><option value="all">Все</option></select></label>
      <label><input v-model="includeExcluded" type="checkbox"> Включить исключённые в JSON-экспорт</label>
      <label>С<input v-model="since"  type="datetime-local"></label><label>По<input v-model="until" type="datetime-local"></label>
      <div class="actions"><button @click="offset=0;safely(loadHistory)">Применить</button><button class="secondary" @click="safely(()=>exportData())">Экспорт серии JSON</button></div>
    </div>
    </details>
    <p role="status">Всего: {{nTotal}} · Исключено: {{nExcluded}} · В анализе: {{nTotal-nExcluded}}</p>
    <details><summary>Сводка по режимам</summary>
    <p class="small">Сводка всей выборки (до 1000 запусков), по режиму, версии, устройству и одинаковым настройкам. E2E — только успешные; успехи — от всех неисключённых запусков, включая прерванные. Показ исключённых в истории не включает их в расчёт.</p>
    <article v-for="s in summaries" :key="s.key"><strong>{{labels[s.mode]}} · {{s.protocol}}</strong><p>Успешно {{s.success}} / {{s.n}} ({{Math.round(s.success/s.n*100)}}%)</p><p>p50 {{ms(s.p50)}} · p95 {{ms(s.p95)}}</p><p>OCR {{s.ocr_calls}} · API {{s.api_calls}}</p>
      <p class="small"><strong>Полнота изображений:</strong> {{s.complete_frames??0}} / {{s.frame_reviewed??0}} · FCR {{s.frame_completeness_rate==null?'нет оценок':Math.round(s.frame_completeness_rate*100)+'%'}}</p>
      <p class="small">Оценка OCR (вспомогательная, не качество захвата): {{s.quality_reviewed??0}} · качество {{s.quality_successes??0}}.</p>
      <p class="small">CPU обработки (Python + дочерний процесс, оценка): {{cpu(s.ocr_cpu_per_run_ms)}} на запуск · {{cpu(s.ocr_cpu_per_complete_scan_ms)}} на подтверждённый полный результат.</p>
      <p class="small">Пик памяти Tesseract: {{sizeMb(s.tesseract_child_rss_peak_bytes)}} · Python + Tesseract: {{sizeMb(s.ocr_process_tree_rss_peak_bytes)}}.</p>
      <p class="small">JSON-трафик OCR + lookup: {{sizeKb(s.transfer_per_run_bytes)}} на запуск · {{sizeKb(s.bytes_per_complete_scan)}} на полный результат. Трафик оценочный: без TLS, сжатия и служебных запросов стенда.</p>
      <p class="small">CPU общего процесса (старый диагностический показатель): {{cpu(s.shared_process_cpu_per_run_ms)}}.</p></article>
    </details>
    <article v-for="r in history" :key="r.id" :class="{excluded:r.excluded_from_analysis}"><p v-if="r.excluded_from_analysis" class="badge">Исключён: {{reasons[r.exclusion_reason||'']}}</p><strong>{{ labels[r.mode] }}</strong><p>{{new Date(r.started_at).toLocaleString()}} · {{statuses[r.outcome||r.status]||r.outcome||r.status}}</p><p class="small">{{r.id}}</p>
      <div class="actions"><button @click="safely(()=>openRun(r.id))">Подробнее</button><button class="secondary" @click="safely(()=>exportData(r.id,r.excluded_from_analysis))">JSON</button><button class="secondary" :disabled="r.status==='running'" @click="askExclusion(r)">{{r.excluded_from_analysis?'Вернуть в анализ':'Исключить из анализа'}}</button><button class="danger" :disabled="r.status==='running'||(runId===r.id&&(outbox.length>0||!!pendingFinish||flushing))" @click="askDeletion(r)">Удалить</button></div></article>
    <div class="actions"><button :disabled="offset===0" @click="offset-=30;safely(loadHistory)">Назад</button><span>{{total?offset+1:0}}–{{offset+history.length}} / {{total}}</span><button :disabled="offset+history.length>=total" @click="offset+=30;safely(loadHistory)">Далее</button></div>
    <article v-if="detail" id="run-detail">
      <button class="secondary" @click="detail=null">Закрыть подробности</button>
      <h2>Подробности {{detail.case?.product_label}}</h2><p class="small">{{detail.id}} · {{detail.protocol_version}} · расположение: {{detail.environment.label_layout??'не указано'}}</p>
      <p class="small">Эталонных ингредиентов в момент теста: {{detailReference.length}}. Критерий качественного результата — 80%.</p>
      <button class="secondary" @click="safely(()=>openRun(detail!.id))">Обновить состояние</button>
      <p v-if="detail.excluded_from_analysis" class="badge">Исключён из анализа: {{reasons[detail.exclusion_reason||'']}}. {{detail.exclusion_comment}}</p>
      <div class="actions"><button class="secondary" :disabled="detail.status==='running'||detail.tasks?.some(t=>t.status==='running')" @click="askExclusion(detail)">{{detail.excluded_from_analysis?'Вернуть в анализ':'Исключить из анализа'}}</button><button class="danger" :disabled="detail.status==='running'||detail.tasks?.some(t=>t.status==='running')||(runId===detail.id&&(outbox.length>0||!!pendingFinish||flushing))" @click="askDeletion(detail)">Удалить</button></div>
      <details v-if="detail.exclusion_audit?.length"><summary>История исключений</summary><p v-for="a in detail.exclusion_audit" :key="a.id">{{new Date(a.created_at).toLocaleString()}} · {{a.action==='exclude'?'Исключён':'Восстановлен'}} · {{reasons[a.reason]}} {{a.comment}}</p></details>
      <details><summary>Время, задачи и события</summary>
      <p v-if="detail.status==='running'">Запуск не завершён.</p>
      <button v-if="detail.status==='running'&&!active" @click="safely(async()=>{await write('/runs/'+detail!.id+'/finish',{outcome:'cancelled',summary:{interruption:'operator_recovery'}});await openRun(detail!.id);await loadHistory();})">Отметить как прерванный</button>
      <label><input type="checkbox" :checked="detail.needs_review" @change="safely(async()=>{await write('/runs/'+detail!.id+'/review',{needs_review:!detail!.needs_review},'PUT');await openRun(detail!.id);})"> Требуется проверка состава</label>
      <div v-for="t in timings" :key="t.label" class="timing"><span>{{t.label}}</span><strong>{{ms(t.value)}}</strong><meter v-if="typeof t.value==='number'" :value="t.value" :max="detail.config.runMaxMs"/></div>
      <p>OCR: {{detail.tasks?.filter(t=>t.kind==='ocr').length}} · lookup: {{detail.tasks?.filter(t=>t.kind==='lookup').length}}</p>
      <div v-if="detail.measured_summary" class="small">
        <p><strong>Трафик запросов OCR + lookup:</strong> {{sizeKb(detail.measured_summary.total_transfer_bytes)}} · OCR отправка {{sizeKb(detail.measured_summary.ocr_upload_bytes)}} / получение {{sizeKb(detail.measured_summary.ocr_download_bytes)}}.</p>
        <p>Штрихкод отправка {{sizeKb(detail.measured_summary.lookup_upload_bytes)}} / получение {{sizeKb(detail.measured_summary.lookup_download_bytes)}} · JPEG {{sizeKb(detail.measured_summary.ocr_image_bytes)}}.</p>
        <p>Обработка кадров на клиенте: {{cpu(detail.measured_summary.client_capture_processing_ms)}} · выбор лучшего кадра: {{cpu(detail.measured_summary.client_frame_selection_ms)}}. Это время работы JS, а не точное CPU устройства.</p>
      </div>
      <section v-for="t in detail.tasks" :key="t.id" class="task"><h3>{{t.kind}} · {{t.status}}</h3><p class="small">{{t.id}}</p>
        <p>Сервер: {{ms(t.metrics.server_elapsed_ms)}} · OCR worker: {{ms(t.metrics.ocr_worker_ms)}}</p>
        <p v-if="t.kind==='ocr'">CPU Python-потока: {{cpu(t.metrics.ocr_python_thread_cpu_ms)}} · CPU дочернего Tesseract (оценка): {{cpu(t.metrics.tesseract_child_cpu_time_ms)}} · всего: {{cpu(t.metrics.ocr_cpu_time_ms)}}.</p>
        <p v-if="t.kind==='ocr'">Пик RAM Tesseract (наблюдаемый): {{sizeMb(t.metrics.tesseract_child_rss_peak_bytes)}} · Python + Tesseract: {{sizeMb(t.metrics.ocr_process_tree_rss_peak_bytes)}}.</p>
        <p v-if="t.kind==='ocr'" class="small">Измерение CPU дочерних процессов разделяется с другими процессами, запущенными API. Пики RAM получены периодическим опросом.</p>
        <p v-if="t.kind==='ocr'">RSS общего процесса (наблюдаемый пик): {{typeof t.metrics.shared_process_rss_peak_bytes==='number'?(t.metrics.shared_process_rss_peak_bytes/1048576).toFixed(1)+' МБ':'Не измерено'}}</p>
        <p>HTTP внешнего API: {{t.metrics.http_status??'—'}} · качество измерения: {{t.metrics.measurement_quality??'not_measured'}}</p>
        <p v-if="t.kind==='ocr'&&t.metrics.capture" class="small">Отбор кадра: {{JSON.stringify(t.metrics.capture)}}</p>
        <div v-if="t.kind==='ocr'" class="capture-archive">
          <p class="small">Изображение, отправленное в OCR (для новых запусков):</p>
          <img :src="frameImage(detail.id,t.id)" alt="Сохранённый кадр OCR" loading="lazy" class="saved-capture" @error="($event.target as HTMLImageElement).style.display='none'"/>
          <div v-if="detailReference.length&&t.result" class="quality-review">
            <p><strong>Ручная проверка rawText</strong> · {{t.quality_review?.matched_count??0}} / {{detailReference.length}}</p>
            <p class="small">Отмечайте ингредиент, если он читается в rawText, даже с небольшой опечаткой. Не используйте compositionText и matched.</p>
            <div v-if="qualityDrafts[t.id]" class="quality-form">
              <label v-for="(ingredient,index) in detailReference" :key="index" class="quality-checkbox">
                <input type="checkbox" :checked="qualityDrafts[t.id]?.matched_indices.includes(index)" @change="toggleQuality(t.id,index,($event.target as HTMLInputElement).checked)"/>
                <span>{{ingredient}}</span>
              </label>
              <label>Полностью ли состав виден на кадре?
                <select :value="qualityDrafts[t.id]?.frame_complete===true?'yes':qualityDrafts[t.id]?.frame_complete===false?'no':'unknown'"
                  @change="qualityDrafts[t.id]!.frame_complete=($event.target as HTMLSelectElement).value==='unknown'?null:($event.target as HTMLSelectElement).value==='yes'">
                  <option value="unknown">Не проверено</option><option value="yes">Да</option><option value="no">Нет</option>
                </select>
              </label>
              <label>Заметки<textarea v-model="qualityDrafts[t.id]!.note" maxlength="1000"/></label>
              <button class="secondary" @click="safely(()=>saveQuality(t.id))">Сохранить оценку OCR</button>
              <p v-if="t.quality_review" class="small">Полнота {{Math.round(t.quality_review.ingredient_recall*100)}}% · {{t.quality_review.quality_success?'порог достигнут':'порог не достигнут'}}</p>
            </div>
          </div>
          <p v-else-if="detailReference.length===0" class="small">Эталон не был записан для этого запуска. Добавьте его в карточку товара перед следующим тестом.</p>
        </div>
        <p v-if="t.cancellation?.cancel_requested">Отмена запрошена. Эффективность: {{t.cancellation?.cancellation_effective?'подтверждена':'вычисления не прерываются'}}. Worker завершён: {{t.cancellation?.worker_stopped?'да':'не подтверждено'}}.</p>
        <details><summary>Результат и метрики</summary><pre>{{JSON.stringify({result:t.result,metrics:t.metrics},null,2)}}</pre></details>
      </section>
      <details><summary>Все события</summary><pre>{{JSON.stringify(detail.events,null,2)}}</pre></details>
      </details>
    </article>
    </section>
    <dialog ref="exclusionDialog" aria-labelledby="exclusion-title">
      <form @submit.prevent="saveExclusion">
        <h2 id="exclusion-title">{{exclusionTarget?.excluded_from_analysis?'Вернуть запуск в анализ?':'Исключить запуск из анализа?'}}</h2>
        <p>Исходные результаты сохраняются. Действие попадёт в журнал.</p>
        <template v-if="!exclusionTarget?.excluded_from_analysis">
          <p class="notice">Исключайте только нарушения протокола. Ошибка OCR, тайм-аут и отказ API сами по себе — обычные результаты эксперимента.</p>
          <label>Причина<select v-model="exclusionReason" required><option value="" disabled>Выберите причину</option><option v-for="(label,value) in reasons" :key="value" :value="value">{{label}}</option></select></label>
          <label>Комментарий<textarea v-model="exclusionComment" maxlength="2000" :required="exclusionReason==='other'"/></label>
        </template>
        <p v-if="exclusionError" class="error" role="alert">{{exclusionError}}</p>
        <div class="actions"><button :disabled="exclusionBusy">{{exclusionBusy?'Сохраняем…':'Подтвердить'}}</button><button type="button" class="secondary" :disabled="exclusionBusy" @click="exclusionDialog?.close()">Отмена</button></div>
      </form>
    </dialog>
    <dialog ref="deletionDialog" aria-labelledby="deletion-title">
      <form @submit.prevent="confirmDeletion">
        <h2 id="deletion-title">Удалить запуск навсегда?</h2>
        <p class="small">{{deletionTarget?.case?.product_label||'Эксперимент'}} · {{deletionTarget?.id}}</p>
        <p class="delete-warning">Запуск, OCR/lookup-задачи, события и история исключений будут физически удалены из базы данных. Восстановить через стенд нельзя. Для обычных неудачных распознаваний лучше использовать «Исключить из анализа».</p>
        <label>Введите УДАЛИТЬ для подтверждения<input v-model="deletionConfirmation" autocomplete="off" spellcheck="false" required placeholder="УДАЛИТЬ" /></label>
        <p v-if="deletionError" class="error" role="alert">{{deletionError}}</p>
        <div class="actions"><button class="danger" :disabled="deletionBusy||deletionConfirmation!=='УДАЛИТЬ'">{{deletionBusy?'Удаление…':'Удалить навсегда'}}</button><button type="button" class="secondary" :disabled="deletionBusy" @click="deletionDialog?.close()">Отмена</button></div>
      </form>
    </dialog>
  </template>
  <p v-if="error" class="error" role="alert">{{error}}</p>
</section>
</template>

<style scoped>
.lab{padding:24px 18px;color:#17201d;overflow-wrap:anywhere}
h1{font-size:23px;margin:14px 0}h2{font-size:19px;margin:22px 0 12px}h3{font-size:16px}
header p,.small{font-size:12px;color:#63776e}a{color:#167a5e}
.notice,.instruction{padding:14px;border-radius:14px;background:#e7f5ef;line-height:1.6;font-size:13px}
.instruction{border:1px solid #a6d7c4}
fieldset,article,.filters{border:1px solid #dce5e0;border-radius:14px;padding:14px;margin:16px 0;background:white;min-width:0}
legend{font-weight:700}label{display:grid;gap:7px;font-size:13px;margin:12px 0}
input,select,textarea{width:100%;min-width:0;padding:11px;border:1px solid #bdcec5;border-radius:8px;font:inherit;background:white;color:inherit}
input[type=checkbox]{width:20px;height:20px}button{padding:12px 14px;background:#187f5d;color:white;border:0;border-radius:9px;font:inherit;font-size:13px;cursor:pointer;min-height:44px;margin:5px 0}
button:disabled{opacity:.45;cursor:default}.secondary{background:#edf4f0;color:#205740}
.danger{background:#ae263a;color:white}.delete-warning{padding:12px;border-radius:10px;background:#fff0f1;color:#80192a;line-height:1.55}
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.actions>*{min-width:0}
.tabs{display:flex;gap:6px;position:sticky;top:0;background:#f5f8f6;padding:8px 0;z-index:2}.tabs button{flex:1;background:#e7efeb;color:#315447}.tabs button[aria-selected=true]{background:#187f5d;color:white}
dialog{margin:auto;inset:0;width:min(440px,calc(100vw - 24px));max-height:85dvh;overflow:auto;border:1px solid #cadbd2;border-radius:18px;padding:20px;color:inherit}dialog::backdrop{background:#10271c99}.excluded{border-style:dashed;background:#f5f5f3}.badge{font-size:13px;color:#805b14}.rotate{width:100%}
.preflight{border:1px solid #cadbd2;border-radius:12px;padding:12px 14px;margin-bottom:12px;background:#f7faf9}
.layout-question{margin:10px 0;padding:12px;border-radius:10px}
.layout-question label,.quality-checkbox{display:flex;align-items:center;gap:10px;margin:8px 0}
.layout-question input[type=radio]{width:18px;height:18px;flex:0 0 18px}
.quality-checkbox input[type=checkbox]{width:18px;height:18px;flex:0 0 18px}
.saved-capture{display:block;width:100%;max-width:560px;max-height:520px;object-fit:contain;margin:8px auto;border:1px solid #dae5e1;border-radius:8px}
.capture-archive,.quality-review{padding:10px;margin-top:12px;background:#f7faf9;border:1px solid #dae5e1;border-radius:10px}
.camera-frame{position:relative;container-type:size;aspect-ratio:var(--frame-ratio);width:min(100%,calc(68dvh * var(--frame-ratio)));max-width:100%;margin:0 auto;overflow:hidden;background:#17201d;border-radius:16px}
.camera-frame video{position:absolute;left:50%;top:50%;max-width:none;max-height:none;display:block;object-fit:fill;transform-origin:center}
.photo-preview{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#17201d}
.camera-controls{position:absolute;z-index:3;bottom:0;left:0;right:0;display:flex;justify-content:center;align-items:center;flex-wrap:wrap;gap:8px;padding:20px 12px max(12px, env(safe-area-inset-bottom));background:linear-gradient(transparent,rgba(12,28,22,.82))}
.camera-controls button{margin:0;min-height:48px;max-width:100%;box-shadow:0 2px 8px #0004}
.camera-controls .capture-button{width:100%;max-width:290px;font-weight:700}
.camera-controls .secondary{background:#fff;color:#205740}
img{max-width:100%;object-fit:contain}summary{cursor:pointer;padding:10px 0}
.error{color:#ae263a;background:#fff0f1;padding:12px;border-radius:10px}pre{white-space:pre-wrap;word-break:break-word;font-size:11px;max-height:400px;overflow:auto}
.events{max-height:300px;overflow:auto;font-size:12px;line-height:1.8;padding-left:20px}
.task{border-top:1px solid #dce5e0;padding:12px 0;font-size:13px}.timing{display:grid;grid-template-columns:1fr auto;gap:8px;font-size:12px;margin:12px 0}.timing meter{width:100%;grid-column:1/-1}
:focus-visible{outline:2px solid #187f5d;outline-offset:3px}
</style>
