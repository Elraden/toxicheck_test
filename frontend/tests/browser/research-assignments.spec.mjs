import {test,expect} from '@playwright/test';
const enrollment='11111111-1111-4111-8111-111111111111';
const participant='22222222-2222-4222-8222-222222222222';
const session={user:{id:'guest',role:'user'},csrf:'test',admin:null,admin_csrf:null};
async function mock(page,completed=false){
 const data={id:enrollment,study_id:'study',study_title:'Проверка этикеток',participant_number:127,exclusions:[{e_code:'E216'}],
  tasks:[1,2].map(n=>({id:'task'+n,position:n,title:'Этикетка '+n,run_id:completed?'run'+n:null,stage:completed?'completed':null})),
  rating:null,rating_available:completed};
 const state={data,ratings:[],starts:[],exchanged:false};
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/auth/session')return route.fulfill({json:session});
  if(path==='/api/research/invitations/exchange'){state.exchanged=true;return route.fulfill({json:{enrollment_id:enrollment}});}
  if(path==='/api/research/enrollments/'+enrollment)return route.fulfill({json:data});
  if(path.endsWith('/rating')){
   state.ratings.push(route.request().postDataJSON());
   data.rating=state.ratings.at(-1).score;
   return route.fulfill({json:{saved:true}});
  }
  if(path==='/api/research/runs'&&route.request().method()==='POST'){
   state.starts.push(route.request().postDataJSON());
   return route.fulfill({json:{id:'run1'}});
  }
  if(path==='/api/research/runs/run1')return route.fulfill({json:{id:'run1',participant_id:participant,participant_number:127,enrollment_id:enrollment,
   stage:'manual_ready',scenario:'comparison',revision:0,barcode_required:true,exclusions:[{e_code:'E216'}],attempts:[],photos:[],instruction:'Изучите упаковку'}});
  return route.fulfill({json:[]});
 });
 return state;
}
test('personal link becomes a task list and preserves order and assigned identity',async({page})=>{
 const state=await mock(page);
 await page.goto('/app/research/invitation#token='+'a'.repeat(43));
 await expect(page).toHaveURL('/app/research/tasks/'+enrollment);
 await expect(page.getByText('Участник P-000127')).toBeVisible();
 expect(state.exchanged).toBe(true);
 const buttons=page.getByRole('button',{name:'Начать задание'});
 await expect(buttons.nth(0)).toBeDisabled();
 await page.getByRole('checkbox').check();
 await expect(buttons.nth(0)).toBeEnabled();
 await expect(buttons.nth(1)).toBeDisabled();
 await buttons.nth(0).click();
 await expect(page.getByRole('heading',{name:'Шаг 1 из 4 · Самостоятельная проверка'})).toBeVisible();
 expect(state.starts[0].assignment_id).toBe('task1');
 await page.getByRole('link',{name:'Мои задания и прогресс'}).click();
 await expect(page.getByText('Участник P-000127')).toBeVisible();
});
test('rating is offered once after all tasks and zero can be submitted',async({page})=>{
 const state=await mock(page,true);
 await page.goto('/app/research/tasks/'+enrollment);
 await expect(page.getByRole('button',{name:'Сохранить оценку'})).toBeDisabled();
 await page.getByRole('radio',{name:'0',exact:true}).check();
 await page.getByRole('button',{name:'Сохранить оценку'}).click();
 await expect(page.getByText('Ваша оценка удовлетворённости: 0 из 10.',{exact:false})).toBeVisible();
 expect(state.ratings).toEqual([{score:0}]);
 await page.reload();
 await expect(page.getByRole('button',{name:'Сохранить оценку'})).toHaveCount(0);
});
test('gallery sends its image to the same OCR endpoint with an explicit source',async({page})=>{
 let submitted=null;
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/auth/session')return route.fulfill({json:session});
  if(path.startsWith('/api/preferences/'))return route.fulfill({json:{excludedIngredientIds:[],revision:0,localImported:true,unresolvedIds:[]}});
  if(path==='/api/scan/composition'){
   submitted=route.request().postDataJSON();
   return route.fulfill({json:{status:'needs_retake',requestId:'gallery-test',captureSource:'gallery',
    recognizedText:'Тестовая этикетка',message:'Переснимите этикетку',tokens:[]}});
  }
  return route.fulfill({json:[]});
 });
 await page.goto('/app/scan');
 await page.getByRole('button',{name:'Состав',exact:true}).click();
 await expect(page.getByRole('button',{name:'Выбрать из галереи'})).toBeVisible();
 const png=await page.evaluate(()=>{
  const c=document.createElement('canvas');c.width=640;c.height=480;
  const ctx=c.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,640,480);
  ctx.fillStyle='black';ctx.font='28px sans-serif';ctx.fillText('Состав: вода',20,100);return c.toDataURL('image/png').split(',')[1];
 });
 await page.locator('input[type=file]:not([capture])').setInputFiles({name:'label.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
 await expect(page.getByRole('button',{name:'Распознать состав',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Распознать состав',exact:true}).click();
 await expect.poll(()=>submitted?.captureSource).toBe('gallery');
 expect(submitted.imageBase64.length).toBeGreaterThan(100);
});

test('Barcode interruption is saved independently and allows the labeling stage',async({page})=>{
 const run={id:'barcode-run',participant_id:participant,participant_number:127,enrollment_id:enrollment,
  stage:'barcode_active',scenario:'comparison',revision:2,barcode_required:true,barcode_started_at:new Date().toISOString(),
  exclusions:[{e_code:'E216'}],attempts:[],photos:[],barcode_attempts:[],manual_answers:{E216:'absent'},app_answers:{E216:'present'}};
 const actions=[];
 await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  if(path==='/api/auth/session')return route.fulfill({json:session});
  if(path.endsWith('/barcode/attempts')&&request.method()==='POST'){
   const attempt={...request.postDataJSON(),status:'scanning',detection_ms:null,lookup_ms:null,total_ms:null};
   run.barcode_attempts.push(attempt);return route.fulfill({json:attempt});
  }
  if(path.endsWith('/finish')){
   const item=run.barcode_attempts[0];item.status=request.postDataJSON().outcome;item.total_ms=50;
   return route.fulfill({json:item});
  }
  if(path.endsWith('/displayed'))return route.fulfill({json:{saved:true}});
  if(path.endsWith('/stage')){
   actions.push(request.postDataJSON());run.stage='labeling';run.revision++;
   return route.fulfill({json:run});
  }
  if(path==='/api/research/runs/barcode-run')return route.fulfill({json:run});
  return route.fulfill({json:[]});
 });
 await page.goto('/app/research/barcode-run');
 await expect(page.getByRole('heading',{name:'Какие добавки присутствуют?'})).toHaveCount(0);
 const finish=page.getByRole('button',{name:'Завершить Barcode и перейти к разметке'});
 await expect(finish).toBeDisabled();
 await page.getByRole('button',{name:'Начать сканирование штрихкода'}).click();
 await page.getByRole('button',{name:'Остановить попытку'}).click();
 await expect(page.getByText('Попытка прервана',{exact:true})).toBeVisible();
 await expect(finish).toBeEnabled();
 await finish.click();
 await expect(page.getByRole('heading',{name:'Перенесите состав с этикетки'})).toBeVisible();
 expect(actions.at(-1).action).toBe('finish_barcode');
 expect(run.manual_answers).toEqual({E216:'absent'});
});
