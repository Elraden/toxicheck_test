<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,ref} from 'vue';
import {useCameraScanner} from '@/composables/useCameraScanner';
import {researchWrite,duration,type BarcodeAttempt} from '@/services/research';
const props=defineProps<{runId:string;attempts:BarcodeAttempt[]}>();
const emit=defineEmits<{updated:[]}>();
const {videoElement,startBarcodeScanning,stopCamera}=useCameraScanner();
const current=ref<BarcodeAttempt|null>(null),error=ref(''),working=ref(false),scanning=ref(false),now=ref(performance.now());
let started=0,generation=0,timeout:ReturnType<typeof setTimeout>|undefined;
const ticker=setInterval(()=>now.value=performance.now(),500);
const names:Record<string,string>={scanning:'Поиск кода',lookup:'Загрузка товара',success:'Состав получен',
 product_not_found:'Товар не найден',composition_missing:'Товар найден без состава',no_code:'Код не найден',
 camera_denied:'Доступ к камере запрещён',camera_error:'Камера недоступна',network_error:'Ошибка сети',
 service_error:'Ошибка сервиса',timeout:'Время ожидания истекло',interrupted:'Попытка прервана'};
const visibleAttempts=computed(()=>current.value&&!props.attempts.some(a=>a.id===current.value!.id)?[...props.attempts,current.value]:props.attempts.map(a=>a.id===current.value?.id?current.value:a));
const blocked=computed(()=>props.attempts.some(a=>['scanning','lookup'].includes(a.status)));
function stop(){clearTimeout(timeout);stopCamera();scanning.value=false;}
async function displayed(value:BarcodeAttempt){
 current.value=value;await nextTick();
 if(!started||['scanning','lookup'].includes(value.status))return;
 await new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r())));
 await researchWrite('/runs/'+props.runId+'/barcode/attempts/'+value.id+'/displayed',{app_e2e_ms:Math.round(performance.now()-started)});
}
async function finish(outcome:string,id=current.value?.id){
 if(!id)return;
 generation++;stop();working.value=true;error.value='';
 try{
  const result=await researchWrite<BarcodeAttempt>('/runs/'+props.runId+'/barcode/attempts/'+id+'/finish',{outcome});
  if(current.value?.id===id)await displayed(result);
  emit('updated');
 }catch(e){error.value=e instanceof Error?e.message:String(e);}
 finally{working.value=false;}
}
async function start(){
 if(working.value||blocked.value)return;
 const version=++generation;
 working.value=true;error.value='';started=performance.now();current.value=null;
 try{
  current.value=await researchWrite<BarcodeAttempt>('/runs/'+props.runId+'/barcode/attempts',{id:crypto.randomUUID()});
  if(version!==generation)return;
  scanning.value=true;await nextTick();
  if(version!==generation)return;
  // The protocol's scan timeout includes camera permission time.
  timeout=setTimeout(()=>{if(version===generation)void finish('no_code');},30000);
  await startBarcodeScanning(async code=>{
   if(version!==generation)return;
   generation++;stop();
   try{
    const result=await researchWrite<BarcodeAttempt>('/runs/'+props.runId+'/barcode/attempts/'+current.value!.id+'/lookup',{barcode:code});
    await displayed(result);emit('updated');
   }catch(e){error.value=e instanceof Error?e.message:String(e);emit('updated');}
   finally{working.value=false;}
  });
  if(version!==generation)stopCamera();
 }catch(e){
  if(version!==generation)return;
  if(current.value)await finish(e instanceof DOMException&&e.name==='NotAllowedError'?'camera_denied':'camera_error');
  else{error.value=e instanceof Error?e.message:String(e);working.value=false;emit('updated');}
 }
}
onBeforeUnmount(()=>{generation++;stop();clearInterval(ticker);});
</script>
<template>
 <section class="research-card">
  <h2>Проверка штрихкода</h2>
  <p>Ответы ручной проверки и OCR уже зафиксированы. Наведите камеру на штрихкод того же товара.</p>
  <p>На поиск кода отводится 30 секунд на попытку. Все повторы и ошибки сохраняются отдельно.</p>
  <video ref="videoElement" v-show="scanning" autoplay muted playsinline style="width:100%;max-height:320px;object-fit:contain"></video>
  <p v-if="working">Время попытки: {{duration(now-started)}}</p>
  <button :disabled="working||blocked" class="research-primary" @click="start">Начать сканирование штрихкода</button>
  <button v-if="scanning" @click="finish('interrupted')">Остановить попытку</button>
  <p v-if="error" class="research-error" role="alert">{{error}}</p>
  <article v-for="item in visibleAttempts" :key="item.id" class="research-card">
   <strong>{{names[item.status]||item.status}}</strong><p v-if="item.barcode">Код: {{item.barcode}}</p>
   <p v-if="item.error">{{item.error}}</p>
   <p>Поиск: {{duration(item.detection_ms)}} · Запрос: {{duration(item.lookup_ms)}} · Всего: {{duration(item.total_ms)}}</p>
   <button v-if="['scanning','lookup'].includes(item.status)" :disabled="working" @click="finish('interrupted',item.id)">Отметить прерывание после потери связи</button>
   <template v-if="item.result"><p>{{item.result.productName||item.result.message}}</p><p>{{item.result.ingredientsText}}</p>
    <p v-if="item.result.analysis">{{item.result.analysis.verdict.title}}. {{item.result.analysis.verdict.description}}</p></template>
  </article>
 </section>
</template>
