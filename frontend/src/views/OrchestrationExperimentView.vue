<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { BrowserMultiFormatReader, type IScannerControls } from '@zxing/browser';
import { BarcodeFormat, DecodeHintType } from '@zxing/library';
import { refreshSession } from '@/services/userSession';
import { Orchestrator, defaults, modes, realClock, validateConfig, type Mode, type Config, type Event, type Task, type Finish } from '@/research/orchestration/engine';
import { capture, type FrameState } from '@/research/orchestration/capture';
import { read, write, download } from '@/research/orchestration/api';

type Case = {id:string;product_label:string;expected_barcode:string|null;label_layout:string};
type Run = {id:string;case_id:string;mode:Mode;status:string;outcome:string|null;valid_result:boolean;protocol_version:string;config:Config;started_at:string;summary:{client_observations?:Record<string,unknown>};needs_review:boolean;events?:Event[];tasks?:Task[];case?:Case};
const statuses:Record<string,string>={waiting:'Ожидание',idle:'Не запущен',running:'Выполняется',detected:'Найден',failed:'Не найден',stopped_for_photo:'Переход к фото',timeout:'Тайм-аут',error:'Ошибка',sufficient:'Состав получен',insufficient:'Недостаточно данных',valid_barcode:'Состав по штрихкоду',valid_ocr:'Состав через OCR',incomplete:'Недостаточно данных',cancelled:'Прерван оператором',technical_failure:'Техническая ошибка',finished:'Завершён',interrupted:'Прерван'};
const labels:Record<Mode,string>={'photo_sequential':'Фото · последовательно','photo_adaptive':'Фото · адаптивно','video_sequential':'Видео · последовательно','video_parallel':'Видео · параллельно','video_adaptive':'Видео · адаптивно'};
const authorized=ref(false),loading=ref(true),error=ref(''),saveError=ref(''),busy=ref(false),active=ref(false);
const mode=ref<Mode>('photo_sequential'),config=ref<Config>({...defaults});
const cases=ref<Case[]>([]),selectedCase=ref(''),product=ref(''),expected=ref(''),layout=ref('unknown'),notes=ref(''),device=ref('');
const cameraPanel=ref<HTMLElement>();
const video=ref<HTMLVideoElement>(),preview=ref(''),runId=ref(''),trace=ref<Event[]>([]),version=ref(0);
const result=ref<Task|null>(null),outcome=ref(''),history=ref<Run[]>([]),total=ref(0),offset=ref(0),detail=ref<Run|null>(null);
const filterMode=ref(''),filterCase=ref(''),filterOutcome=ref(''),since=ref(''),until=ref('');
let engine:Orchestrator|undefined,stream:MediaStream|undefined,controls:IScannerControls|undefined;
let frameTimer:ReturnType<typeof setInterval>|undefined,flushTimer:ReturnType<typeof setInterval>|undefined;
let frameState:FrameState={previous:null,lastSent:null},outbox:Event[]=[],flushing=false,pendingFinish:Finish|undefined;
let destroyed=false,generation=0;
const tasks=new Map<string,boolean>();
const taskKinds=new Map<string,string>();
const state=computed(()=>{void version.value;return engine;});
const isVideo=computed(()=>mode.value.startsWith('video'));
const videoInstruction='Покажите камере штрихкод и текст состава. Если они расположены на разных сторонах упаковки, поверните товар и наведите камеру на состав. Снимок делается автоматически.';
const key='toxicheck-orchestration-pending-v3';
function message(e:unknown){return e instanceof Error?e.message:String(e);}
function stash(){
  try{sessionStorage.setItem(key,JSON.stringify({id:runId.value,events:outbox,finish:pendingFinish}));}
  catch{saveError.value='Не удалось сохранить локальную очередь. Не закрывайте страницу до синхронизации.';}
}
async function flush(){
  if(flushing||!runId.value)return;flushing=true;
  try{
    const batch=outbox.slice(0,100);
    await write('/runs/'+runId.value+'/events',{events:batch});
    outbox.splice(0,batch.length);stash();
    if(!outbox.length&&pendingFinish){
      await write('/runs/'+runId.value+'/finish',pendingFinish);pendingFinish=undefined;stash();
      await loadHistory();
    }
    saveError.value='';
  }catch(e){saveError.value='Данные ещё не синхронизированы: '+message(e);}
  finally{flushing=false;}
}
function emit(e:Event){trace.value.push(e);outbox.push(e);stash();}
function stopBarcode(){controls?.stop();controls=undefined;}
function stopCamera(){stopBarcode();stream?.getTracks().forEach(t=>t.stop());stream=undefined;if(frameTimer)clearInterval(frameTimer);}
function cancelTasks(kind?:string){
  for(const [id,running] of tasks)if(running&&(!kind||taskKinds.get(id)===kind))void write('/runs/'+runId.value+'/tasks/'+id+'/cancel',{}).catch(e=>{saveError.value=message(e);});
}
async function task(kind:'lookup'|'ocr',value:string):Promise<Task>{
  const id=crypto.randomUUID(),current=runId.value,start=performance.now();
  tasks.set(id,true);taskKinds.set(id,kind);
  let row:Task;
  const payload={id,...(kind==='ocr'?{image_base64:value}:{barcode:value})};
  try{
    row=await write<Task>('/runs/'+current+'/'+kind,payload);
    // Durable task polling is separate from HTTP acknowledgement of the submission.
    engine?.event(kind==='ocr'?'ocr_submission_ack':'lookup_submission_ack',{task_id:id,http_status:200,submission_ms:performance.now()-start});
    for(let n=0;row.status==='running'&&n<240;n++){
      await new Promise(resolve=>setTimeout(resolve,500));
      row=await read<Task>('/runs/'+current+'/tasks/'+id);
      if(destroyed)throw new Error('Страница закрыта; серверная задача сохраняет результат самостоятельно.');
    }
    if(row.status==='running')throw new Error('Серверная задача ещё работает. Проверьте историю.');
    if(current===runId.value)engine?.event(kind==='ocr'?'ocr_worker_observed':'lookup_response_observed',{
      task_id:id,http_ms:performance.now()-start,metrics:row.metrics});
    return row;
  }finally{tasks.set(id,false);}
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
    if(outbox.length||pendingFinish)throw new Error('Сначала синхронизируйте предыдущий запуск.');
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('Нужны HTTPS и браузер с доступом к камере.');
    runId.value=crypto.randomUUID();trace.value=[];tasks.clear();frameState={previous:null,lastSent:null};preview.value='';
    // Request permission in the user-gesture flow, but persist run even if permission fails.
    const cameraPromise=navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}})
      .then(s=>{acquired=s;return {stream:s,error:null};}).catch(e=>({stream:null,error:e}));
    try{
      await write('/runs',{id:runId.value,case_id:selectedCase.value,mode:mode.value,config:config.value,
        environment:{user_agent:navigator.userAgent,device_label:device.value,started_at_utc:new Date().toISOString(),zxing_browser_version:'0.2.1',
          label_layout:cases.value.find(c=>c.id===selectedCase.value)?.label_layout}});
    }catch(e){void cameraPromise.then(r=>r.stream?.getTracks().forEach(t=>t.stop()));throw e;}
    engine=new Orchestrator(mode.value,config.value,{
      clock:realClock,emit,lookup:code=>task('lookup',code),ocr:image=>task('ocr',image),cancel:()=>cancelTasks(),cancelLookup:()=>cancelTasks('lookup'),
      stopBarcode,changed:()=>{version.value++;},finished:(v,t)=>{void finished(v,t);}
    },started);
    active.value=true;engine.start();stash();
    const ready=await cameraPromise;
    if(token!==generation||destroyed||engine.done){ready.stream?.getTracks().forEach(t=>t.stop());return;}
    if(ready.error||!ready.stream)throw new Error('Камера недоступна: '+message(ready.error));
    stream=ready.stream;
    await nextTick();
    if(!video.value)throw new Error('Не удалось открыть просмотр камеры.');
    video.value.srcObject=stream;await video.value.play();engine.ready();
    const settings=stream.getVideoTracks()[0]?.getSettings();
    engine.event('camera_settings',{width:settings?.width,height:settings?.height,frame_rate:settings?.frameRate,facing_mode:settings?.facingMode});
    cameraPanel.value?.scrollIntoView({block:'start',behavior:'smooth'});
    const hints=new Map();hints.set(DecodeHintType.POSSIBLE_FORMATS,[BarcodeFormat.EAN_13,BarcodeFormat.EAN_8,BarcodeFormat.UPC_A,BarcodeFormat.UPC_E]);
    const reader=new BrowserMultiFormatReader(hints,{delayBetweenScanAttempts:200,delayBetweenScanSuccess:500});
    controls=await reader.decodeFromVideoElement(video.value,(decoded,_error,ctrl)=>{
      if(engine?.done||token!==generation){ctrl.stop();return;}
      if(decoded){engine?.detected(decoded.getText());ctrl.stop();}
    });
    if(engine.done||engine.barcodeState!=='running')stopBarcode();
    frameTimer=setInterval(()=>{
      if(!engine||engine.done||!isVideo.value||!engine.canCapture()||!video.value)return;
      engine.event('frame_considered');
      const frame=capture(video.value,frameState,true);
      if(frame&&engine.submit(frame.image)){frameState.lastSent=frame.fingerprint;}
    },config.value.frameMinIntervalMs);
  }catch(e){
    error.value=message(e);
    if(engine&&!engine.done)engine.finish('technical_failure');
    acquired?.getTracks().forEach(t=>t.stop());
  }finally{busy.value=false;}
}
function takePhoto(){
  if(!engine||!video.value)return;
  engine.selectPhoto();
  const frame=capture(video.value,frameState,false);
  if(frame)preview.value=frame.image;else error.value='Дождитесь изображения камеры и попробуйте снова.';
}
function confirmPhoto(){if(preview.value&&engine?.submit(preview.value,true))preview.value='';}
async function addCase(){
  error.value='';try{
    const item=await write<Case>('/cases',{product_label:product.value,expected_barcode:expected.value||null,label_layout:layout.value,notes:notes.value});
    cases.value.unshift(item);selectedCase.value=item.id;
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
async function loadHistory(){
  const q=query();q.set('offset',String(offset.value));
  const data=await read<{items:Run[];total:number}>('/runs?'+q);history.value=data.items;total.value=data.total;
  summaries.value=(await read<{groups:Aggregate[]}>('/summary?'+query())).groups;
}
async function safely(fn:()=>Promise<unknown>){try{error.value='';await fn();}catch(e){error.value=message(e);}}
async function openRun(id:string){detail.value=await read<Run>('/runs/'+id);}
async function exportData(id?:string){
  const data=await read(id?'/runs/'+id+'/export.json':'/export.json?'+query());
  download(data,'toxicheck-orchestration-'+(id||'series')+'.json');
}
function ms(value:unknown){return typeof value==='number'&&Number.isFinite(value)?(value/1000).toFixed(2)+' с':'Не измерено';}
type Aggregate={key:string;mode:Mode;protocol:string;n:number;success:number;p50:number|null;p95:number|null;ocr_calls:number;api_calls:number;shared_process_cpu_per_run_ms:number|null;shared_process_cpu_per_success_ms:number|null;shared_process_rss_peak_bytes:number|null};
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
  document.addEventListener('visibilitychange',visibility);
  try{
    authorized.value=!!(await refreshSession()).admin;
    if(!authorized.value)return;
    cases.value=await read<Case[]>('/cases?limit=100');
    selectedCase.value=cases.value[0]?.id??'';
    const saved=sessionStorage.getItem(key);
    if(saved){
      const pending=JSON.parse(saved);runId.value=pending.id??'';outbox=pending.events??[];pendingFinish=pending.finish;
      if(runId.value){
        await flush();
        const previous=await read<Run>('/runs/'+runId.value);
        if(previous.status==='running'&&!pendingFinish){
          pendingFinish={outcome:'cancelled',winning_task_id:null,summary:{interruption:'page_reloaded',end_to_end_ms:null}};
          await flush();
        }
      }
    }
    await loadHistory();
    flushTimer=setInterval(()=>{if(runId.value&&(active.value||outbox.length||pendingFinish))void flush();},2000);
  }catch(e){error.value=message(e);}finally{loading.value=false;}
});
onBeforeUnmount(()=>{
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
    <p class="notice">Пилот v3: пригодность ответа не означает подтверждённую точность состава. Сравнивайте один товар в разных режимах, чередуя их порядок.</p>
    <fieldset :disabled="active||busy"><legend>Товар и условия</legend>
      <label>Товар<select v-model="selectedCase"><option value="">Выберите товар</option><option v-for="c in cases" :key="c.id" :value="c.id">{{ c.product_label||c.id }}</option></select></label>
      <p v-if="selectedCase" class="small">case_id: {{ selectedCase }}</p>
      <details><summary>Добавить товар</summary>
        <label>Название<input v-model="product" maxlength="200"></label>
        <label>Ожидаемый штрихкод — только для сверки<input v-model="expected" inputmode="numeric" maxlength="14"></label>
        <label>Штрихкод и состав<select v-model="layout"><option value="unknown">Не указано</option><option value="same_side">На одной стороне</option><option value="different_side">На разных сторонах</option></select></label>
        <label>Заметки<textarea v-model="notes" maxlength="2000"/></label><button @click="addCase">Сохранить товар</button>
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
    <p ref="cameraPanel" class="instruction">{{ isVideo?videoInstruction:'Покажите штрихкод. Если данных недостаточно, понадобится фотография состава. При разных сторонах упаковки поверните товар к составу.' }}</p>
    <video ref="video" muted playsinline autoplay aria-label="Камера эксперимента"/>
    <p v-if="state" role="status">Штрихкод: {{ statuses[state.barcodeState]||state.barcodeState }} · lookup: {{ statuses[state.lookupState]||state.lookupState }} · OCR: {{ statuses[state.ocrState]||state.ocrState }}</p>
    <button v-if="!active" :disabled="busy||!!saveError" @click="start">{{ busy?'Подготовка…':'Начать эксперимент' }}</button>
    <template v-if="active">
      <div class="actions"><button class="secondary" @click="engine?.event('composition_visible')">Состав появился в кадре</button><button class="secondary" @click="engine?.event('package_turned')">Повернул упаковку</button></div>
      <button v-if="state?.photoHint" :disabled="!state?.camera||state?.ocrState==='running'" @click="takePhoto">Сфотографировать состав</button>
      <div v-if="preview"><img :src="preview" alt="Подтверждение фотографии состава"><div class="actions"><button :disabled="!state?.canCapture()" @click="confirmPhoto">Подтвердить фото</button><button class="secondary" @click="preview=''">Переснять</button></div></div>
      <button class="secondary" @click="engine?.finish('cancelled')">Остановить запуск</button>
    </template>
    <article v-if="outcome"><h2>Результат: {{ statuses[outcome]||outcome }}</h2><p v-if="result?.sufficient">Получен пригодный состав</p>
      <p>{{ result?.result?.ingredientsText || result?.result?.compositionText || 'Пригодный состав не получен.' }}</p>
      <p class="small">Оставшаяся серверная работа учитывается отдельно. Обновите подробности запуска после её завершения.</p>
      <button @click="safely(()=>openRun(runId))">Подробности запуска</button>
    </article>
    <p v-if="saveError" class="error" role="alert">{{ saveError }} <button @click="flush">Повторить сохранение</button></p>
    <details v-if="trace.length"><summary>Хронология текущего запуска ({{trace.length}})</summary><ol class="events"><li v-for="e in trace" :key="e.seq">{{ ms(e.relative_ms) }} · {{ e.event_name }}</li></ol></details>
    <h2>История и выгрузка</h2>
    <div class="filters">
      <label>Режим<select v-model="filterMode"><option value="">Все</option><option v-for="m in modes" :key="m" :value="m">{{labels[m]}}</option></select></label>
      <label>Товар<select v-model="filterCase"><option value="">Все</option><option v-for="c in cases" :key="c.id" :value="c.id">{{c.product_label||c.id}}</option></select></label>
      <label>Результат<select v-model="filterOutcome"><option value="">Все</option><option v-for="o in ['valid_barcode','valid_ocr','incomplete','timeout','cancelled','technical_failure']" :key="o" :value="o">{{statuses[o]||o}}</option></select></label>
      <label>С<input v-model="since" type="datetime-local"></label><label>По<input v-model="until" type="datetime-local"></label>
      <div class="actions"><button @click="offset=0;safely(loadHistory)">Применить</button><button class="secondary" @click="safely(()=>exportData())">Экспорт серии JSON</button></div>
    </div>
    <p class="small">Сводка всей выборки (до 1000 запусков), по режиму, версии, устройству и одинаковым настройкам. E2E — только успешные; успехи — от всех запусков, включая прерванные.</p>
    <article v-for="s in summaries" :key="s.key"><strong>{{labels[s.mode]}} · {{s.protocol}}</strong><p>Успешно {{s.success}} / {{s.n}} ({{Math.round(s.success/s.n*100)}}%)</p><p>p50 {{ms(s.p50)}} · p95 {{ms(s.p95)}}</p><p>OCR {{s.ocr_calls}} · API {{s.api_calls}}</p><p class="small">Оценка CPU общего процесса на запуск: {{ms(s.shared_process_cpu_per_run_ms)}}; на успешный: {{ms(s.shared_process_cpu_per_success_ms)}}. Это не точные затраты OCR.</p></article>
    <article v-for="r in history" :key="r.id"><strong>{{ labels[r.mode] }}</strong><p>{{new Date(r.started_at).toLocaleString()}} · {{statuses[r.outcome||r.status]||r.outcome||r.status}}</p><p class="small">{{r.id}}</p>
      <div class="actions"><button @click="safely(()=>openRun(r.id))">Подробнее</button><button class="secondary" @click="safely(()=>exportData(r.id))">JSON</button></div></article>
    <div class="actions"><button :disabled="offset===0" @click="offset-=30;safely(loadHistory)">Назад</button><span>{{offset+1}}–{{offset+history.length}} / {{total}}</span><button :disabled="offset+history.length>=total" @click="offset+=30;safely(loadHistory)">Далее</button></div>
    <article v-if="detail">
      <h2>Подробности {{detail.case?.product_label}}</h2><p class="small">{{detail.id}} · {{detail.protocol_version}}</p>
      <button class="secondary" @click="safely(()=>openRun(detail!.id))">Обновить состояние</button>
      <p v-if="detail.status==='running'">Запуск не завершён.</p>
      <button v-if="detail.status==='running'&&!active" @click="safely(async()=>{await write('/runs/'+detail!.id+'/finish',{outcome:'cancelled',summary:{interruption:'operator_recovery'}});await openRun(detail!.id);await loadHistory();})">Отметить как прерванный</button>
      <label><input type="checkbox" :checked="detail.needs_review" @change="safely(async()=>{await write('/runs/'+detail!.id+'/review',{needs_review:!detail!.needs_review},'PUT');await openRun(detail!.id);})"> Требуется проверка состава</label>
      <div v-for="t in timings" :key="t.label" class="timing"><span>{{t.label}}</span><strong>{{ms(t.value)}}</strong><meter v-if="typeof t.value==='number'" :value="t.value" :max="detail.config.runMaxMs"/></div>
      <p>OCR: {{detail.tasks?.filter(t=>t.kind==='ocr').length}} · lookup: {{detail.tasks?.filter(t=>t.kind==='lookup').length}}</p>
      <section v-for="t in detail.tasks" :key="t.id" class="task"><h3>{{t.kind}} · {{t.status}}</h3><p class="small">{{t.id}}</p>
        <p>Сервер: {{ms(t.metrics.server_elapsed_ms)}} · OCR worker: {{ms(t.metrics.ocr_worker_ms)}}</p>
        <p v-if="t.kind==='ocr'">CPU общего процесса: {{ms(t.metrics.shared_process_cpu_delta_ms)}}. Дочерний Tesseract сюда не включён.</p>
        <p v-if="t.kind==='ocr'">RSS общего процесса (наблюдаемый пик): {{typeof t.metrics.shared_process_rss_peak_bytes==='number'?(t.metrics.shared_process_rss_peak_bytes/1048576).toFixed(1)+' МБ':'Не измерено'}}</p>
        <p>HTTP внешнего API: {{t.metrics.http_status??'—'}} · качество измерения: {{t.metrics.measurement_quality??'not_measured'}}</p>
        <p v-if="t.cancellation?.cancel_requested">Отмена запрошена. Эффективность: {{t.cancellation?.cancellation_effective?'подтверждена':'вычисления не прерываются'}}. Worker завершён: {{t.cancellation?.worker_stopped?'да':'не подтверждено'}}.</p>
        <details><summary>Результат и метрики</summary><pre>{{JSON.stringify({result:t.result,metrics:t.metrics},null,2)}}</pre></details>
      </section>
      <details><summary>Все события</summary><pre>{{JSON.stringify(detail.events,null,2)}}</pre></details>
    </article>
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
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.actions>*{min-width:0}
video{width:100%;max-height:45dvh;background:#17201d;border-radius:16px;object-fit:contain}
img{max-width:100%;max-height:45dvh;object-fit:contain}summary{cursor:pointer;padding:10px 0}
.error{color:#ae263a;background:#fff0f1;padding:12px;border-radius:10px}pre{white-space:pre-wrap;word-break:break-word;font-size:11px;max-height:400px;overflow:auto}
.events{max-height:300px;overflow:auto;font-size:12px;line-height:1.8;padding-left:20px}
.task{border-top:1px solid #dce5e0;padding:12px 0;font-size:13px}.timing{display:grid;grid-template-columns:1fr auto;gap:8px;font-size:12px;margin:12px 0}.timing meter{width:100%;grid-column:1/-1}
:focus-visible{outline:2px solid #187f5d;outline-offset:3px}
</style>
