<script setup lang="ts">
import {onMounted,ref} from 'vue';
import {request} from '@/services/http';
import {ensureGuestSession} from '@/services/userSession';
import {stageNames,type RunSummary} from '@/services/research';
import '@/research.css';
const runs=ref<RunSummary[]>([]),assignments=ref<{id:string;participant_number:number;title:string}[]>([]),error=ref(''),busy=ref(false);
async function load(){
 busy.value=true;error.value='';
 try{
  await ensureGuestSession();
  [runs.value,assignments.value]=await Promise.all([request<RunSummary[]>('/research/runs'),request<typeof assignments.value>('/research/enrollments')]);
 }catch(e){error.value=e instanceof Error?e.message:String(e);}
 finally{busy.value=false;}
}
onMounted(load);
</script>
<template>
 <section class="research-shell">
  <RouterLink to="/app/home">← На главную</RouterLink><h1>Мои исследования</h1>
  <p>Откройте персональную ссылку организатора. Она сохраняет ваш номер участника и задания при переходе на другое устройство.</p>
  <p v-if="busy" role="status">Загружаем…</p><p v-if="error" role="alert" class="research-error">{{error}} <button @click="load">Повторить</button></p>
  <RouterLink v-for="item in assignments" :key="item.id" class="research-card research-choice" :to="{name:'research-tasks',params:{enrollmentId:item.id}}">
   <strong>{{item.title}}</strong><span>Участник P-{{String(item.participant_number).padStart(6,'0')}}</span><span>Задания и прогресс →</span>
  </RouterLink>
  <p v-if="!busy&&!error&&!assignments.length">Пока нет доступных назначений. Используйте выданную персональную ссылку.</p>
  <details v-if="runs.length" class="research-card"><summary>Ранее начатые проверки</summary>
   <RouterLink v-for="run in runs" :key="run.id" class="research-list-row" :to="{name:'research-run',params:{runId:run.id}}">
    <strong>{{run.title}}</strong><span>{{stageNames[run.stage]}}</span>
   </RouterLink>
  </details>
 </section>
</template>
