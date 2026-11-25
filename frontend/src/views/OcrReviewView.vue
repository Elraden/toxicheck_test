<script setup lang="ts">
import {onMounted,ref} from 'vue';
import {request} from '@/services/http';
import {refreshSession,csrfHeaders} from '@/services/userSession';
import {getIngredients,type IngredientSummary} from '@/services/backendApi';
type Review={review_id:string;token_raw:string;candidate_canonical_name:string;match_score:number;is_hazard:boolean;was_accepted:boolean};
const allowed=ref(false),rows=ref<Review[]>([]),total=ref(0),offset=ref(0),error=ref(''),busy=ref(false),token=ref<string|null>(null);
const correcting=ref<Review|null>(null),search=ref(''),choices=ref<IngredientSummary[]>([]),selected=ref('');
async function load(){
 const result=await request<{items:Review[];total:number}>('/admin/ocr/reviews?offset='+offset.value);
 rows.value=result.items;total.value=result.total;
 if(offset.value&&offset.value>=total.value){offset.value=Math.max(0,offset.value-30);await load();}
}
async function safely(fn:()=>Promise<unknown>){
 if(busy.value)return;busy.value=true;error.value='';
 try{await fn();}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}
}
async function label(row:Review,correct:boolean,id?:string){
 await request('/admin/ocr/reviews/'+row.review_id,{method:'PUT',headers:csrfHeaders(token.value),
   body:JSON.stringify({isCorrect:correct,correctedIngredientId:id||null})});
 correcting.value=null;await load();
}
function edit(row:Review){correcting.value=row;search.value='';selected.value='';choices.value=[];}
onMounted(()=>safely(async()=>{
 const session=await refreshSession();allowed.value=!!session.admin;token.value=session.admin_csrf;
 if(allowed.value)await load();
}));
</script>
<template>
<section class="reviews">
 <RouterLink :to="{name:'admin'}">← Админ-режим</RouterLink><h1>Проверка OCR</h1>
 <p v-if="!allowed">Требуется вход администратора.</p>
 <template v-else>
  <p>Сомнительные совпадения и пользовательские исправления. Только подтверждённая разметка записывается для будущего обучения; каталог и модель автоматически не меняются.</p>
  <p>Ожидают проверки: {{total}}</p>
  <article v-for="row in rows" :key="row.review_id">
   <strong v-if="row.is_hazard" class="hazard">Проверьте возможный E-код</strong>
   <h2>«{{row.token_raw}}»</h2><p>Предложено: {{row.candidate_canonical_name}}</p>
   <p class="meta">Оценка совпадения: {{Number(row.match_score).toFixed(2)}} · {{row.was_accepted?'Принято эвристикой':'Не принято'}}</p>
   <div v-if="correcting?.review_id!==row.review_id" class="actions">
    <button :disabled="busy" @click="safely(()=>label(row,true))">Верно</button>
    <button :disabled="busy" @click="safely(()=>label(row,false))">Неверно</button>
    <button :disabled="busy" @click="edit(row)">Выбрать исправление</button>
   </div>
   <form v-else @submit.prevent="safely(()=>label(row,false,selected))">
    <label>Ингредиент<input v-model="search" placeholder="Название или E-код"></label>
    <button type="button" :disabled="busy||!search.trim()" @click="safely(async()=>{choices=(await getIngredients(search,'all',0)).items;selected='';})">Найти</button>
    <label>Правильное название<select v-model="selected" required><option value="" disabled>Выберите ингредиент</option><option v-for="item in choices" :key="item.id" :value="item.id">{{item.code}} {{item.name}}</option></select></label>
    <button :disabled="busy||!selected">Сохранить исправление</button>
    <button type="button" @click="correcting=null">Отмена</button>
   </form>
  </article>
  <p v-if="!rows.length&&!busy">Очередь пуста.</p>
  <div class="actions"><button :disabled="busy||offset===0" @click="offset-=30;safely(load)">Назад</button><button :disabled="busy||offset+rows.length>=total" @click="offset+=30;safely(load)">Далее</button></div>
 </template>
 <p v-if="error" role="alert" class="hazard">{{error}}</p>
</section>
</template>
<style scoped>
.reviews{padding:24px 18px;color:#17201d;overflow-wrap:anywhere}h1{font-size:23px;margin:16px 0}h2{font-size:18px}p{line-height:1.5;margin:12px 0}
article{background:white;border:1px solid #dce5e0;border-radius:16px;padding:16px;margin:16px 0}.meta{font-size:12px;color:#52665a}.hazard{color:#923e18}
button{padding:12px;border:0;border-radius:8px;background:#187f5d;color:white;min-height:44px;margin:4px 0;font:inherit;font-size:13px}button:disabled{opacity:.5}.actions{display:flex;gap:8px;flex-wrap:wrap}
label{display:grid;gap:8px;margin:10px 0}input,select{min-width:0;width:100%;padding:12px;border:1px solid #bdcec5;border-radius:8px;font:inherit}a{color:#167a5e}
</style>
