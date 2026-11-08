import {test,expect} from '@playwright/test';

test.afterEach(async({page})=>{
  await page.evaluate(()=>document.querySelectorAll('video').forEach(v=>v.srcObject?.getTracks().forEach(t=>t.stop()))).catch(()=>{});
  await page.goto('about:blank',{timeout:5000}).catch(()=>{});
});

// Only this test intercepts API traffic. Production keeps real OCR/OFF services.
async function fixture(page,{video=false}={}){
 const item={id:'11111111-1111-4111-8111-111111111111',product_label:'Тестовая этикетка',label_layout:'different_side'};
 const state={runs:[],events:[],images:[],finish:null,ocr:null,admin:true};
 await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
   let body={};
   if(path==='/api/auth/session')body={user:null,admin:state.admin?{id:'admin',role:'admin'}:null,admin_csrf:'test'};
   else if(path.endsWith('/cases'))body=[item];
   else if(path.endsWith('/summary'))body={groups:[],n_total:state.runs.length,n_excluded:state.runs.filter(r=>r.excluded_from_analysis).length};
   else if(path.endsWith('/runs')&&method==='POST'){
     body={...req.postDataJSON(),status:'running',protocol_version:'live-v3.0',summary:{},started_at:new Date().toISOString(),excluded_from_analysis:false,tasks:[],events:[]};
     state.runs.push(body);
   }
   else if(path.endsWith('/runs')){
     const status=url.searchParams.get('exclusion_status');
     const items=state.runs.filter(r=>status==='all'||(status==='excluded'?r.excluded_from_analysis:!r.excluded_from_analysis));
     body={items,total:items.length};
   }
   else if(path.endsWith('/events')){state.events.push(...req.postDataJSON().events);body={saved:true};}
   else if(path.endsWith('/finish')){state.finish=req.postDataJSON();Object.assign(state.runs.at(-1),state.finish,{status:'finished'});body=state.runs.at(-1);}
   else if(path.endsWith('/ocr')){
     state.images.push(req.postDataJSON().image_base64);
     state.ocr={...req.postDataJSON(),kind:'ocr',status:'running',sufficient:false,metrics:{}};
     body=state.ocr;
   }
   else if(path.includes('/tasks/')&&!path.endsWith('/cancel')){
     body=video?state.ocr:{...state.ocr,status:'finished',sufficient:true,result:{compositionText:'Вода, сахар, соль'},metrics:{}};
   }
   else if(path.endsWith('/exclusion')){
     expect(req.headers()['x-csrf-token']).toBe('test');
     const id=path.split('/').at(-2),run=state.runs.find(r=>r.id===id),payload=req.postDataJSON();
     Object.assign(run,{excluded_from_analysis:payload.excluded,exclusion_reason:payload.reason,exclusion_comment:payload.comment});body=run;
   }
   else if(path.endsWith('/cancel'))body={acknowledged:true};
   else body={...state.runs.at(-1),case:item};
   await route.fulfill({json:body});
 });
 await page.goto('/app/admin/orchestration');
 await expect(page.getByRole('tab',{name:'Настройки',exact:true})).toHaveAttribute('aria-selected','true');
 return state;
}
async function configure(page,mode){
 await page.locator('#lab-panel-settings').getByLabel('Режим',{exact:false}).selectOption(mode);
 await page.getByText('Настройки пилота, мс',{exact:true}).click();
 await page.getByLabel('Поиск штрихкода',{exact:true}).fill('15000');
 await page.getByLabel('Подсказка фото',{exact:true}).fill('0');
 await page.getByLabel('Ранний OCR',{exact:true}).fill('0');
 await page.getByLabel('Весь запуск',{exact:true}).fill('120000');
 await page.getByRole('tab',{name:'Камера',exact:true}).click();
}
test('canvas rotation preserves colored corner pixels and derives every preview from the original',async({page})=>{
 await fixture(page);
 const result=await page.evaluate(async()=>{
   const {rotateCanvas,photoImage}=await import('/src/research/orchestration/capture.ts');
   const results=[];
   for(const [w,h] of [[640,480],[480,640],[641,479]]){
     const source=document.createElement('canvas');source.width=w;source.height=h;
     const ctx=source.getContext('2d');
     ctx.fillStyle='#ffffff';ctx.fillRect(0,0,w,h);
     const colors=['#ff0000','#00ff00','#0000ff','#ffff00'];
     for(const [i,[x,y]] of [[0,0],[w-16,0],[w-16,h-16],[0,h-16]].entries()){
       ctx.fillStyle=colors[i];ctx.fillRect(x,y,16,16);
     }
     const previews=[];
     for(const angle of [0,90,180,270,360]){
       const canvas=rotateCanvas(source,angle),c=canvas.getContext('2d');
       const corners=[[4,4],[canvas.width-5,4],[canvas.width-5,canvas.height-5],[4,canvas.height-5]]
         .map(([x,y])=>Array.from(c.getImageData(x,y,1,1).data));
       results.push({angle,w,h,width:canvas.width,height:canvas.height,corners});
       previews.push(photoImage(source,angle));
     }
     if(previews[0]!==previews[4])throw new Error('Repeated rotations degraded original');
   }
   return results;
 });
 const original=[[255,0,0,255],[0,255,0,255],[0,0,255,255],[255,255,0,255]];
 for(const r of result){
   const turns=r.angle/90%4;
   expect(r.width).toBe(turns%2?r.h:r.w);expect(r.height).toBe(turns%2?r.w:r.h);
   expect(r.corners).toEqual(original.map((_,i)=>original[(i-turns+4)%4]));
 }
});
test('capture and confirmation stay visible over the camera on a phone-sized screen',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await fixture(page);
 await configure(page,'photo_adaptive');
 await page.getByRole('button',{name:'Начать эксперимент',exact:true}).click();
 const camera=page.locator('.camera-frame');
 const capture=camera.getByRole('button',{name:'Сфотографировать состав',exact:true});
 await expect(capture).toBeVisible();
 await expect(capture).toBeInViewport({ratio:1});
 await capture.click();
 const confirm=camera.getByRole('button',{name:'Подтвердить фото',exact:true});
 const retake=camera.getByRole('button',{name:'Переснять',exact:true});
 await expect(confirm).toBeVisible();
 await expect(confirm).toBeInViewport({ratio:1});
 await expect(retake).toBeVisible();
 await retake.click();
 await expect(capture).toBeVisible();
 await expect(confirm).toHaveCount(0);
});
test('photo preview exactly matches submitted bytes; orientation events retain the run',async({page})=>{
 const state=await fixture(page);
 await configure(page,'photo_adaptive');
 await page.getByRole('button',{name:'Начать эксперимент',exact:true}).click();
 await page.getByRole('button',{name:'Сфотографировать состав',exact:true}).click();
 const preview=page.getByAltText('Подтверждение фотографии состава');
 const first=await preview.getAttribute('src');
 for(let n=0;n<4;n++)await page.getByRole('button',{name:/Повернуть превью/}).click();
 await expect(preview).toHaveAttribute('src',first);
 await page.getByRole('button',{name:/Повернуть превью/}).click();
 const rotated=await preview.getAttribute('src');
 expect(rotated).not.toBe(first);
 await page.getByRole('button',{name:'Подтвердить фото',exact:true}).click();
 await expect(page.getByText('Получен пригодный состав',{exact:true})).toBeVisible();
 await expect.poll(()=>state.finish?.outcome).toBe('valid_ocr');
 expect(state.images).toEqual([rotated]);expect(state.runs).toHaveLength(1);
 expect(state.events.filter(e=>e.event_name==='preview_rotation_changed').map(e=>e.details.angle)).toEqual([90,180,270,0,90]);
 expect(state.runs[0].environment.initial_frame_rotation).toBe(0);
});
test('video rotation preserves stream, timer and in-flight request limits',async({page})=>{
 // Stable high-detail test source: Chromium's moving fake camera is deliberately
 // rejected by the real motion/contrast gate on some browser versions.
 await page.addInitScript(()=>{
   navigator.mediaDevices.getUserMedia=async()=>{
     const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;
     const ctx=canvas.getContext('2d');
     for(let y=0;y<480;y+=20)for(let x=0;x<640;x+=20){
       ctx.fillStyle=(x+y)%40?'#333333':'#dddddd';ctx.fillRect(x,y,20,20);
     }
     const stream=canvas.captureStream(10);
     const timer=setInterval(()=>ctx.fillRect(0,0,1,1),100);
     stream.getVideoTracks()[0].addEventListener('ended',()=>clearInterval(timer));
     return stream;
   };
 });
 const state=await fixture(page,{video:true});
 await configure(page,'video_parallel');
 await page.getByRole('button',{name:'Начать эксперимент',exact:true}).click();
 await expect.poll(()=>page.locator('video').evaluate(v=>v.readyState)).toBeGreaterThanOrEqual(2);
 const stream=await page.locator('video').evaluate(v=>({id:v.srcObject.id,track:v.srcObject.getVideoTracks()[0].id}));
 await page.getByRole('button',{name:/Повернуть кадр/}).click();
 await page.getByLabel('Ориентация области камеры',{exact:false}).selectOption('landscape');
 await expect.poll(()=>state.images.length,{timeout:15000}).toBe(1);
 await page.getByRole('button',{name:/Повернуть кадр/}).click();
 await page.waitForTimeout(2000);
 expect(state.images).toHaveLength(1);
 expect(await page.locator('video').evaluate(v=>({id:v.srcObject.id,track:v.srcObject.getVideoTracks()[0].id}))).toEqual(stream);
 expect(state.runs).toHaveLength(1);
 await page.getByRole('button',{name:'Остановить запуск',exact:true}).click();
 await expect.poll(()=>state.finish?.outcome).toBe('cancelled');
 expect(state.events.filter(e=>e.event_name==='run_started')).toHaveLength(1);
 expect(state.events.some(e=>e.event_name==='orientation_selected')).toBe(true);
 expect(state.events.filter(e=>e.event_name==='frame_rotation_changed').map(e=>e.details.angle)).toEqual([90,180]);
});
test('tabs, mobile widths, exclusion confirmation, filters and restoration',async({page},testInfo)=>{
 const state=await fixture(page);
 state.runs.push({id:'finished',mode:'photo_sequential',status:'finished',outcome:'timeout',started_at:new Date().toISOString(),excluded_from_analysis:false});
 await expect(page.getByRole('heading',{name:'История и выгрузка'})).not.toBeVisible();
 for(const width of [320,390,1440]){
   await page.setViewportSize({width,height:844});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('tab',{name:'История',exact:true}).click();
 await page.getByText('Фильтры и экспорт',{exact:true}).click();
 await page.getByRole('button',{name:'Применить',exact:true}).click();
 await page.getByRole('button',{name:'Исключить из анализа',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog).toBeVisible();
 await page.screenshot({path:testInfo.outputPath('exclusion-mobile.png')});
 await dialog.getByLabel('Причина',{exact:false}).selectOption('other');
 await dialog.getByRole('button',{name:'Подтвердить',exact:true}).click();
 await expect(dialog).toBeVisible();expect(state.runs[0].excluded_from_analysis).toBe(false);
 await dialog.getByLabel('Комментарий',{exact:true}).fill('Неверно выбран режим');
 await dialog.getByRole('button',{name:'Подтвердить',exact:true}).click();
 await expect(dialog).not.toBeVisible();
 await expect(page.getByText('Всего: 1 · Исключено: 1 · В анализе: 0',{exact:true})).toBeVisible();
 await page.getByLabel('Показать запуски',{exact:false}).selectOption('excluded');
 await page.getByRole('button',{name:'Применить',exact:true}).click();
 await page.getByRole('button',{name:'Вернуть в анализ',exact:true}).click();
 await dialog.getByRole('button',{name:'Подтвердить',exact:true}).click();
 await expect(page.getByText('Всего: 1 · Исключено: 0 · В анализе: 1',{exact:true})).toBeVisible();
 expect(state.runs[0].outcome).toBe('timeout');
 await page.screenshot({path:testInfo.outputPath('history-mobile.png'),fullPage:true});
});
