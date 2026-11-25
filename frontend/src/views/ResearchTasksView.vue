<script setup lang="ts">
import {ref,watch} from 'vue';
import {useRoute,useRouter} from 'vue-router';
import {request} from '@/services/http';
import {ensureGuestSession} from '@/services/userSession';
import {researchWrite,type ResearchRun,stageNames} from '@/services/research';
import '@/research.css';

type Assignment={id:string;position:number;title:string;run_id:string|null;stage:string|null};
type Enrollment={id:string;study_id:string;study_title:string;participant_number:number;tasks:Assignment[];rating:number|null;rating_available:boolean;exclusions:{e_code:string}[]};
const route=useRoute(),router=useRouter();
const data=ref<Enrollment|null>(null),error=ref(''),busy=ref(false),score=ref<number|null>(null),consent=ref(false);
const finished=(t:Assignment)=>['completed','aborted'].includes(t.stage||'');
async function load(){
 if(busy.value)return;
 busy.value=true;error.value='';
 try{
  await ensureGuestSession();
  const token=new URLSearchParams(route.hash.slice(1)).get('token');
  let id=String(route.params.enrollmentId||'');
  if(token){
   const result=await researchWrite<{enrollment_id:string}>('/invitations/exchange',{token});
   id=result.enrollment_id;
   // Token stays out of server URLs, storage and subsequent navigation.
   await router.replace({name:'research-tasks',params:{enrollmentId:id}});
  }
  if(!id)throw new Error('Откройте персональную ссылку организатора.');
  data.value=await request<Enrollment>('/research/enrollments/'+id);
  score.value=data.value.rating;
 }catch(e){error.value=e instanceof Error?e.message:String(e);}
 finally{busy.value=false;}
}
async function open(task:Assignment){
 if(!data.value||busy.value)return;
 busy.value=true;error.value='';
 try{
  let id=task.run_id;
  if(!id){
   if(!consent.value)throw new Error('Подтвердите согласие на участие.');
   const run=await researchWrite<ResearchRun>('/runs',{id:crypto.randomUUID(),study_id:data.value.study_id,
    assignment_id:task.id,title:task.title,consent:true});
   id=run.id;
  }
  await router.push({name:'research-run',params:{runId:id}});
 }catch(e){error.value=e instanceof Error?e.message:String(e);}
 finally{busy.value=false;}
}
async function rate(){
 if(score.value===null||!data.value||busy.value)return;
 busy.value=true;error.value='';
 try{await researchWrite('/enrollments/'+data.value.id+'/rating',{score:score.value});data.value.rating=score.value;}
 catch(e){error.value=e instanceof Error?e.message:String(e);}
 finally{busy.value=false;}
}
watch(()=>route.params.enrollmentId,()=>void load(),{immediate:true});
</script>
<template>
 <section class="research-shell">
  <RouterLink :to="{name:'research'}">← Мои исследования</RouterLink>
  <h1>{{data?.study_title||'Персональное исследование'}}</h1>
  <p v-if="busy" role="status">Загружаем…</p>
  <p v-if="error" role="alert" class="research-error">{{error}} <button :disabled="busy" @click="load">Повторить</button></p>
  <template v-if="data">
   <p class="research-tag">Участник P-{{String(data.participant_number).padStart(6,'0')}}</p>
   <p>Завершено заданий: {{data.tasks.filter(t=>t.stage==='completed').length}} из {{data.tasks.length}}. Прервано: {{data.tasks.filter(t=>t.stage==='aborted').length}}.</p>
   <p>Исключения исследования: {{data.exclusions.map(x=>x.e_code).join(', ')||'Нет'}}.</p>
   <label v-if="data.tasks.some(t=>!t.run_id)" class="research-check"><input v-model="consent" type="checkbox">Согласен на сохранение фотографий этикеток, ответов и времени выполнения для исследования. Не фотографируйте личные документы и людей.</label>
   <article v-for="(task,index) in data.tasks" :key="task.id" class="research-card">
    <h2>{{task.position}}. {{task.title}}</h2><p>{{task.stage?stageNames[task.stage]:'Ещё не начато'}}</p>
    <button class="research-primary" :disabled="busy||(!task.run_id&&(!consent||data.tasks.slice(0,index).some(t=>!finished(t))))" @click="open(task)">{{finished(task)?'Посмотреть':task.run_id?'Продолжить':'Начать задание'}}</button>
   </article>
   <section v-if="data.rating!==null" class="research-card"><h2>Спасибо!</h2><p>Ваша оценка удовлетворённости: {{data.rating}} из 10. Она сохранена один раз для вашего номера участника.</p></section>
   <form v-else-if="data.rating_available" class="research-card" @submit.prevent="rate">
    <h2>Насколько вы удовлетворены использованием ToxiCheck?</h2>
    <p>0 — совсем не удовлетворён, 10 — полностью удовлетворён.</p>
    <fieldset class="rating"><legend>Выберите оценку от 0 до 10</legend><label v-for="value in 11" :key="value" :class="{selected:score===value-1}"><input v-model="score" type="radio" name="score" :value="value-1">{{value-1}}</label></fieldset>
    <button class="research-primary" :disabled="busy||score===null">Сохранить оценку</button>
   </form>
  </template>
 </section>
</template>
<style scoped>
.rating{border:0;padding:8px 0;display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.rating label{display:flex;align-items:center;justify-content:center;gap:4px;border:1px solid #b7cfc5;border-radius:10px;min-width:56px;min-height:48px;cursor:pointer}
.rating .selected{background:#d9f5e8;border-color:#167a5e}.rating input{accent-color:#167a5e}
</style>
