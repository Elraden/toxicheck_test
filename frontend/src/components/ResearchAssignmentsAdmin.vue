<script setup lang="ts">
import {onMounted,ref} from 'vue';
import {request} from '@/services/http';
import {researchWrite,type Study} from '@/services/research';
const props=defineProps<{studies:Study[]}>();
type Person={id:string;participant_number:number};
type Case={id:string;title:string};
type Enrollment={id:string;title:string;participant_number:number;participant_id:string;finished_count:number;task_count:number;score:number|null};
const people=ref<Person[]>([]),cases=ref<Case[]>([]),selected=ref<Case[]>([]),rows=ref<Enrollment[]>([]);
const participant=ref(''),study=ref(''),personQuery=ref(''),caseQuery=ref(''),caseTitle=ref(''),codes=ref('');
const assignmentDetails=ref<Record<string,{exclusions:{e_code:string}[];tasks:{id:string;position:number;title:string;stage:string|null;run_id:string|null}[]}>>({});
const error=ref(''),busy=ref(false),link=ref(''),offset=ref(0),caseOffset=ref(0);
let creationId=crypto.randomUUID();
const number=(n:number)=>'P-'+String(n).padStart(6,'0');
async function task(fn:()=>Promise<void>){
 if(busy.value)return;busy.value=true;error.value='';
 try{await fn();}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}
}
async function load(){rows.value=await request<Enrollment[]>('/admin/research/enrollments?offset='+offset.value);}
async function searchPeople(){people.value=await request<Person[]>('/admin/research/participants?q='+encodeURIComponent(personQuery.value));}
async function searchCases(more=false){
 caseOffset.value=more?caseOffset.value+50:0;
 const found=await request<Case[]>('/admin/research/cases?q='+encodeURIComponent(caseQuery.value)+'&offset='+caseOffset.value);
 cases.value=more?[...cases.value,...found]:found;
}
function add(item:Case){if(!selected.value.some(c=>c.id===item.id))selected.value.push(item);}
async function createCase(){const item=await researchWrite<Case>('/cases',{title:caseTitle.value},true);add(item);caseTitle.value='';}
async function invite(id:string){
 const value=await researchWrite<{token:string}>('/enrollments/'+id+'/invitation',{},true);
 link.value=new URL('/app/research/invitation#token='+encodeURIComponent(value.token),window.location.origin).href;
}
async function create(){
 const value=await researchWrite<{id:string}>('/enrollments',{id:creationId,study_id:study.value,
  participant_id:participant.value||null,case_ids:selected.value.map(x=>x.id),
  exclusion_codes:codes.value.split(/[,;\s]+/).filter(Boolean)},true);
 creationId=crypto.randomUUID();selected.value=[];
 await load();await searchPeople();await invite(value.id);
}
async function revoke(id:string){await researchWrite('/enrollments/'+id+'/invitation',{},true,'DELETE');link.value='';}
onMounted(()=>task(async()=>{await load();await searchPeople();await searchCases();}));
</script>
<template>
 <details class="research-card">
  <summary>Участники, задания и персональные ссылки</summary>
  <p>Назначьте этикетки в нужном порядке. Номер участника общий для всех исследований; условия назначенного набора фиксируются при создании.</p>
  <p v-if="error" class="research-error" role="alert">{{error}}</p>
  <form class="research-form" @submit.prevent="task(create)">
   <label>Исследование<select v-model="study" required><option value="" disabled>Выберите</option><option v-for="s in props.studies" :key="s.id" :value="s.id">{{s.title}} {{s.is_open?'':'(закрыто)'}}</option></select></label>
   <label>Поиск участника по номеру<input v-model="personQuery" placeholder="P-000127"></label><button type="button" :disabled="busy" @click="task(searchPeople)">Найти участника</button>
   <label>Участник<select v-model="participant"><option value="">Создать нового с новым номером</option><option v-for="p in people" :key="p.id" :value="p.id">{{number(p.participant_number)}}</option></select></label>
   <label>Исключения для всех заданий этого набора<input v-model="codes" placeholder="E202, E211"></label>
   <label>Найти этикетку<input v-model="caseQuery"></label><button type="button" :disabled="busy" @click="task(()=>searchCases())">Найти этикетки</button>
   <div class="case-picker"><button v-for="c in cases" :key="c.id" type="button" :disabled="selected.some(s=>s.id===c.id)||busy" @click="add(c)">+ {{c.title}}</button></div>
   <button v-if="cases.length&&(cases.length%50===0)" type="button" :disabled="busy" @click="task(()=>searchCases(true))">Ещё этикетки</button>
   <label>Название новой этикетки<input v-model="caseTitle" maxlength="200"></label><button type="button" :disabled="busy||!caseTitle.trim()" @click="task(createCase)">Создать и добавить этикетку</button>
   <ol><li v-for="(c,index) in selected" :key="c.id">{{c.title}} <button type="button" @click="selected.splice(index,1)">Убрать</button><button type="button" :disabled="index===0" @click="[selected[index-1],selected[index]]=[selected[index]!,selected[index-1]!]">↑</button></li></ol>
   <button class="research-primary" :disabled="busy||!study||!selected.length">Назначить и получить ссылку</button>
  </form>
  <label v-if="link">Персональная ссылка (сохраните и отправьте участнику)<input :value="link" readonly @focus="($event.target as HTMLInputElement).select()"></label>
  <p v-if="link">Ссылка открывает только этот набор. Перевыпуск отзывает предыдущую ссылку и выданный по ней доступ.</p>
  <h2>Назначения и прогресс</h2>
  <article v-for="r in rows" :key="r.id" class="research-card">
   <strong>{{number(r.participant_number)}} · {{r.title}}</strong>
   <p>Завершено или прервано: {{r.finished_count}} / {{r.task_count}} · Оценка: {{r.score===null?'ещё нет':r.score+'/10'}}</p>
   <details @toggle="($event.target as HTMLDetailsElement).open && task(async()=>{assignmentDetails[r.id]=await request('/admin/research/enrollments/'+r.id);})"><summary>Назначенные этикетки</summary><template v-if="assignmentDetails[r.id]"><p>Исключения: {{assignmentDetails[r.id]!.exclusions.map(x=>x.e_code).join(', ')}}</p><ol><li v-for="t in assignmentDetails[r.id]!.tasks" :key="t.id">{{t.title}} — {{t.stage||'Не начато'}}</li></ol></template></details>
   <button :disabled="busy" @click="task(()=>invite(r.id))">Выдать новую ссылку</button>
   <button :disabled="busy" @click="task(()=>revoke(r.id))">Отозвать доступ</button>
  </article>
  <div class="research-actions"><button :disabled="busy||offset===0" @click="offset-=50;task(load)">Назад</button><button :disabled="busy||rows.length<50" @click="offset+=50;task(load)">Далее</button><button :disabled="busy" @click="task(load)">Обновить прогресс</button></div>
 </details>
</template>
<style scoped>
.case-picker{max-height:220px;overflow:auto;display:grid;gap:6px}li{margin:8px 0}article button{margin:4px}
</style>
