(() => {
  if(globalThis.__toxiInstalled)return;
  globalThis.__toxiInstalled=true;
  const P=globalThis.ToxiPresentation;
  const allowed=()=>['ozon.ru','wildberries.ru','lavka.yandex.ru'].some(d=>location.hostname===d||location.hostname.endsWith('.'+d));
  let panel,shadow,body,sequence=0,viewSequence=0,lastKey='',timer,lastUrl=location.href,analysisState=null,scanning=false,scanAgain=false;
  function el(tag,text,parent,cls){
    const node=document.createElement(tag);
    if(text!==undefined)node.textContent=text;
    if(cls)node.className=cls;
    if(parent)parent.append(node);
    return node;
  }
  function button(parent,text,fn,cls){
    const b=el('button',text,parent,cls);b.type='button';b.addEventListener('click',fn);return b;
  }
  function logo(parent){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('width','36');svg.setAttribute('height','36');svg.setAttribute('viewBox','0 0 80 80');svg.setAttribute('aria-hidden','true');
    const rect=document.createElementNS(svg.namespaceURI,'rect');
    for(const [k,v] of Object.entries({width:80,height:80,rx:16,fill:'#51B992'}))rect.setAttribute(k,v);
    const path=document.createElementNS(svg.namespaceURI,'path');
    for(const [k,v] of Object.entries({d:'M40 23.5 C43.3 26.4 47.3 28.2 52.5 28.9 V41.7 C52.5 49.1 47.5 54.6 40 56.8 C32.5 54.6 27.5 49.1 27.5 41.7 V28.9 C32.7 28.2 36.7 26.4 40 23.5Z',fill:'none',stroke:'#fff','stroke-width':3.5,'stroke-linecap':'round','stroke-linejoin':'round'}))path.setAttribute(k,v);
    svg.append(rect,path);parent.append(svg);
  }
  const styles=chrome.runtime.getURL('panel.css');
  // Keep the chosen position within this tab, including when the panel is reopened.
  let position=null,resizeObserver=null;
  function clampPosition(x,y,w,h,vw,vh){
    const margin=8;
    return {x:Math.max(margin,Math.min(x,Math.max(margin,vw-w-margin))),
      y:Math.max(margin,Math.min(y,Math.max(margin,vh-h-margin)))};
  }
  function place(target,x,y){
    const rect=target.getBoundingClientRect();
    position=clampPosition(x,y,rect.width,rect.height,document.documentElement.clientWidth,window.innerHeight);
    target.style.setProperty('--toxi-x',position.x+'px');
    target.style.setProperty('--toxi-y',position.y+'px');
    target.style.setProperty('--toxi-right','auto');
    target.style.setProperty('--toxi-bottom','auto');
  }
  function fitPanel(){
    if(panel?.isConnected&&position)place(panel,position.x,position.y);
  }
  window.addEventListener('resize',fitPanel);
  function enableDrag(header,target){
    let drag=null;
    header.addEventListener('pointerdown',event=>{
      if(!event.isPrimary||event.button!==0||event.target.closest('button,a,input'))return;
      const rect=target.getBoundingClientRect();
      drag={id:event.pointerId,dx:event.clientX-rect.left,dy:event.clientY-rect.top};
      header.setPointerCapture(event.pointerId);
      header.classList.add('dragging');
      event.preventDefault();
    });
    header.addEventListener('pointermove',event=>{
      if(drag?.id!==event.pointerId)return;
      place(target,event.clientX-drag.dx,event.clientY-drag.dy);
    });
    function finish(event){
      if(drag?.id!==event.pointerId)return;
      drag=null;header.classList.remove('dragging');
    }
    for(const type of ['pointerup','pointercancel','lostpointercapture'])header.addEventListener(type,finish);
    // Keyboard users can move the window using the focused handle and arrow keys.
    header.addEventListener('keydown',event=>{
      if(event.target!==header)return;
      const delta={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]}[event.key];
      if(!delta)return;
      event.preventDefault();
      const rect=target.getBoundingClientRect();place(target,rect.left+delta[0],rect.top+delta[1]);
    });
    header.tabIndex=0;
    header.setAttribute('aria-label','Переместить окно ToxiCheck: перетащите или используйте стрелки клавиатуры');
    header.title='Перетащите окно за заголовок';
  }
  function open(){
    if(panel?.isConnected)return;
    panel=document.createElement('div');
    panel.style.setProperty('visibility','hidden','important');
    shadow=panel.attachShadow({mode:'closed'});
    const link=el('link',undefined,shadow);
    link.rel='stylesheet';
    const target=panel;
    link.onload=()=>{target.style.removeProperty('visibility');if(target===panel)fitPanel();};
    link.onerror=()=>target.style.removeProperty('visibility');
    link.href=styles;
    const shell=el('section',undefined,shadow,'shell');
    shell.setAttribute('aria-label','ToxiCheck — анализ состава');
    const header=el('header',undefined,shell,'top');
    const brand=el('div',undefined,header,'brand');logo(brand);
    const words=el('div',undefined,brand);el('strong','ToxiCheck',words);el('small','Проверка состава продукта',words);
    const grip=el('span','⠿',header,'drag-grip');grip.setAttribute('aria-hidden','true');
    enableDrag(header,panel);
    const close=button(header,'×',()=>{sequence++;viewSequence++;panel.remove();},'close');
    close.setAttribute('aria-label','Закрыть ToxiCheck');
    body=el('div',undefined,shell,'body');
    document.documentElement.append(panel);
    resizeObserver?.disconnect();
    resizeObserver=new ResizeObserver(fitPanel);resizeObserver.observe(panel);
    if(position)fitPanel();
  }
  function badge(parent,s){return el('span',s.label,parent,'badge '+s.tone);}
  function editor(state,expanded){
    const details=el('details',undefined,body,'card editor');details.open=expanded;
    el('summary','Исходный состав',details);
    el('small',state.detected?'Найден автоматически. Проверьте полноту состава.':'Выбран вручную.',details);
    const field=el('textarea',undefined,details);field.value=state.text;field.setAttribute('aria-label','Текст состава');
    field.addEventListener('input',()=>{state.draft=field.value;});
    if(state.draft!==undefined)field.value=state.draft;
    button(details,'Проверить состав',()=>void run(field.value,false),'primary');
  }
  function showResult(state){
    viewSequence++;body.replaceChildren();body.scrollTop=0;
    editor(state,!state.result);
    if(state.error){el('p',state.error,body,'card error');return;}
    if(!state.result){el('p','Выполняется анализ…',body,'loading');return;}
    const {verdict,matched,unmatched}=state.result;
    const tone=verdict.level==='dangerous'||verdict.level==='risky_for_user'?'danger':verdict.level==='attention'?'attention':verdict.level==='safe'?'neutral':'unknown';
    const v=el('article',undefined,body,'card verdict '+tone);
    el('h2',verdict.title,v);el('p',verdict.description,v);
    const personalIds=new Set((verdict.reasons||[]).filter(r=>r.severity==='personal').map(r=>r.ingredient_id));
    const personal=matched.filter(i=>personalIds.has(i.ingredient_id));
    const {additives,foods}=P.groups(matched.filter(i=>!personalIds.has(i.ingredient_id)));
    const counts=el('div',undefined,body,'counts');
    for(const [n,label] of [[matched.filter(P.isAdditive).length,'E-добавки'],[matched.filter(i=>!P.isAdditive(i)).length,'Ингредиенты'],[unmatched.length,'Не распознано']]){
      const c=el('div',undefined,counts,'count');el('strong',String(n),c);el('small',label,c);
    }
    function group(title,items){
      if(!items.length)return;
      el('h3',title+' · '+items.length,body);
      for(const item of items){
        const s=P.status(item.severity,item.rules);
        const excluded=personalIds.has(item.ingredient_id);
        const card=button(body,'',()=>void showIngredient(item,state),'ingredient '+(excluded?'danger':s.tone));
        if(item.code)el('span',item.code,card,'code');
        const info=el('span',undefined,card,'info');el('strong',item.name,info);badge(info,s);
        if(excluded)badge(info,{label:'Ваше исключение',tone:'danger'});
        const reason=verdict.reasons?.find(r=>r.ingredient_id===item.ingredient_id);
        if(reason?.explanation)el('small',reason.explanation,info);
        el('span','›',card,'arrow').setAttribute('aria-hidden','true');
      }
    }
    group('Из ваших исключений',personal);
    group('Пищевые добавки (E-коды)',additives);
    group('Остальные ингредиенты',foods);
    if(unmatched.length){
      const box=el('article',undefined,body,'card');
      el('h4','Не распознано · '+unmatched.length,box);
      el('p','Эти позиции не получили оценку. Проверьте исходный текст.',box,'muted');
      el('p',unmatched.join(', '),box,'unmatched');
    }
    el('small','Оценка основана на каталоге и правилах ToxiCheck. Учитываются сохранённые в расширении персональные исключения. Неполный состав не подтверждает безопасность.',body);
  }
  function detailSection(title,text){
    const box=el('article',undefined,body,'detail-section');el('h3',title,box);if(text)el('p',text,box);return box;
  }
  function source(parent,title,locator,url,verification){
    const block=el('div',undefined,parent,'source');
    if(title)el('h4',P.documentLabel(title),block);
    if(locator)el('p',P.documentLabel(locator),block);
    const href=P.sourceUrl(url);
    if(href){const a=el('a','Открыть документ ↗',block);a.href=href;a.target='_blank';a.rel='noopener noreferrer';}
    if(verification&&verification!=='verified_primary')el('small','Подтверждение по источнику не завершено.',block);
  }
  function renderRules(rules){
    if(!rules?.length)return;
    const box=detailSection('Условия применения и источники');
    for(const rule of rules){
      const r=P.rulePresentation(rule),article=el('div',undefined,box,'rule');
      el('h4',r.title,article);if(r.summary)el('p',r.summary,article);
      if(r.needsReview)el('small','Требует проверки по первоисточнику.',article,'review');
      const details=el('details',undefined,article);el('summary','Основание и источники',details);
      if(rule.assessment_note)el('p',rule.assessment_note,details);
      if(rule.explanation&&rule.explanation!==rule.assessment_note)el('p',rule.explanation,details);
      const evidence=rule.conditions?.evidence;
      if(Array.isArray(evidence)&&evidence.length){
        for(const e of evidence)source(details,e.source_title||rule.source_title,e.locator,e.url,e.verification_status);
      }else source(details,rule.source_title,rule.citation,rule.source_url);
    }
  }
  async function showIngredient(item,state){
    const id=++viewSequence;body.replaceChildren();body.scrollTop=0;
    button(body,'← К результатам анализа',()=>{if(state.preferencesChanged)void run(state.text,false,state.detected);else showResult(state);},'back');
    const title=el('div',undefined,body,'detail-title '+P.status(item.severity,item.rules).tone);
    if(item.code)el('span',item.code,title,'code');
    el('h2',item.name,title);
    const s=P.status(item.severity,item.rules),assessment=detailSection('Оценка');
    badge(assessment,s);el('p',s.description,assessment);
    const preferenceBox=detailSection('Мои предпочтения');
    const toggle=button(preferenceBox,'Загрузка…',()=>{},'');toggle.disabled=true;
    const prefStatus=el('small','',preferenceBox);
    try{
      const {excludedIngredients}=await chrome.storage.local.get({excludedIngredients:[]});
      if(id!==viewSequence)return;
      let selected=excludedIngredients.some(x=>x.id===item.ingredient_id);
      const update=()=>{toggle.textContent=selected?'Убрать из исключений':'Добавить в исключения';toggle.className=selected?'exclude-button selected':'exclude-button';toggle.setAttribute('aria-pressed',String(selected));};
      update();toggle.disabled=false;
      toggle.onclick=async()=>{
        toggle.disabled=true;
        try{
          const r=await chrome.runtime.sendMessage({type:'TOXI_EXCLUSION',item:{id:item.ingredient_id,name:item.name,code:item.code},selected:!selected});
          if(!r?.ok)throw Error(r?.error||'Не удалось сохранить.');
          selected=r.data.some(x=>x.id===item.ingredient_id);update();
          prefStatus.textContent='Сохранено. Вердикт обновится при возврате к результатам.';
          state.preferencesChanged=true;
        }catch(e){prefStatus.textContent=e.message;}finally{toggle.disabled=false;}
      };
    }catch(e){prefStatus.textContent=e.message;}
    if(id!==viewSequence)return;
    const loading=el('p','Загружаем карточку ингредиента…',body,'loading');
    try{
      const response=await chrome.runtime.sendMessage({type:'TOXI_INGREDIENT',id:item.ingredient_id});
      if(id!==viewSequence||!panel?.isConnected)return;
      if(!response?.ok)throw Error(response?.error||'Не удалось загрузить карточку.');
      loading.remove();
      const d=response.data;
      if(d.name_en)el('small',d.name_en,title);
      detailSection('Описание',d.full_description||d.description||'Описание пока не добавлено.');
      if(d.functions?.length)detailSection('Функции',d.functions.join(', '));
      if(d.origins?.length)detailSection('Происхождение',d.origins.join(', '));
      const aliases=[...new Set(d.aliases||[])].filter(x=>x&&x.toLowerCase()!==item.name.toLowerCase()&&x.toLowerCase()!==String(item.code||'').toLowerCase());
      if(aliases.length){
        const box=detailSection('Другие названия');const details=el('details',undefined,box);el('summary','Показать ('+aliases.length+')',details);const list=el('ul',undefined,details);for(const a of aliases)el('li',a,list);
      }
      renderRules(d.rules?.length?d.rules:item.rules);
    }catch(e){
      if(id!==viewSequence||!panel?.isConnected)return;
      loading.remove();el('p',e.message,body,'card error');button(body,'Повторить загрузку',()=>void showIngredient(item,state));renderRules(item.rules);
    }
  }
  async function run(text,automatic=false,detected=automatic){
    open();const id=++sequence;
    const state={text,detected,result:null,error:null};analysisState=state;showResult(state);
    try{
      const response=await chrome.runtime.sendMessage({type:'TOXI_ANALYZE',text,automatic});
      if(id!==sequence)return;
      if(!response?.ok)throw Error(response?.error||'Сервис недоступен.');
      state.result=response.data;showResult(state);
    }catch(e){if(id===sequence){state.error=e.message;showResult(state);}}
  }
  function checkNavigation(){
    if(location.href!==lastUrl){lastUrl=location.href;lastKey='';sequence++;viewSequence++;analysisState=null;panel?.remove();}
  }
  async function scan(){
    checkNavigation();
    if(!allowed())return 'Автоматический режим работает на поддерживаемых площадках.';
    if(scanning){scanAgain=true;return 'Поиск состава уже выполняется.';}
    scanning=true;
    try{
      const {auto}=await chrome.storage.local.get({auto:false});
      if(!auto)return 'Автоматический режим отключён.';
      const result=globalThis.ToxiExtract.find(document);
      if(!result)return 'Состав пока не найден. Откройте описание товара или выделите текст.';
      const key=location.href+'\n'+result.text;
      if(key===lastKey)return 'Состав уже найден.';
      lastKey=key;
      if(analysisState?.text===result.text&&panel?.isConnected&&!analysisState.error&&!analysisState.preferencesChanged)return 'Состав уже найден.';
      void run(result.text,true);
      return 'Состав найден, выполняется анализ.';
    }finally{scanning=false;if(scanAgain){scanAgain=false;schedule();}}
  }
  function schedule(){
    // Throttle instead of an endlessly postponed debounce on dynamic storefronts.
    if(timer)return;
    timer=setTimeout(()=>{timer=null;void scan().catch(console.warn);},800);
  }
  chrome.runtime.onMessage.addListener((msg,_sender,respond)=>{
    if(msg?.type==='TOXI_AUTO_SCAN'){scan().then(message=>respond({ok:true,message}),e=>respond({ok:false,message:e.message}));return true;}
    if(msg?.type==='TOXI_MANUAL'){
      const text=String(msg.text||getSelection()?.toString()||'').trim();
      if(text){void run(text);respond({ok:true});}else respond({ok:false,error:'Выделите текст состава на странице.'});
    }else if(msg?.type==='TOXI_FIND'){
      const found=globalThis.ToxiExtract.find(document);
      if(found){void run(found.text,false,true);respond({ok:true});}else respond({ok:false,error:'Состав не найден. Выделите текст вручную.'});
    }
  });
  chrome.storage.onChanged.addListener((changes,area)=>{
    if(area==='local'&&changes.excludedIngredients){lastKey='';if(analysisState)analysisState.preferencesChanged=true;}
  });
  if(allowed()){
    const observer=new MutationObserver(records=>{
      checkNavigation();
      if(records.some(r=>r.target!==panel&&!panel?.contains(r.target)&&!(r.type==='childList'&&[...r.addedNodes,...r.removedNodes].every(n=>n===panel))))schedule();
    });
    observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['hidden','aria-hidden','open','class','style']});
    // Covers SPA navigation and late product descriptions without relying on DOM events alone.
    setInterval(()=>{if(!document.hidden)schedule();},3000);
    window.addEventListener('popstate',schedule);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)schedule();});
    chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.auto){lastKey='';if(changes.auto.newValue)schedule();}});
    schedule();
  }
})();
