(() => {
  if(globalThis.__toxiInstalled)return;
  globalThis.__toxiInstalled=true;
  const P=globalThis.ToxiPresentation;
  const allowed=()=>['ozon.ru','wildberries.ru','lavka.yandex.ru'].some(d=>location.hostname===d||location.hostname.endsWith('.'+d));
  let panel,shadow,body,sequence=0,viewSequence=0,lastKey='',timer,lastUrl=location.href,analysisState=null;
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
  function open(){
    if(panel?.isConnected)return;
    panel=document.createElement('div');
    panel.style.setProperty('visibility','hidden','important');
    shadow=panel.attachShadow({mode:'closed'});
    const link=el('link',undefined,shadow);
    link.rel='stylesheet';
    link.onload=()=>panel?.style.removeProperty('visibility');
    link.onerror=()=>panel?.style.removeProperty('visibility');
    link.href=styles;
    const shell=el('section',undefined,shadow,'shell');
    shell.setAttribute('aria-label','ToxiCheck — анализ состава');
    const header=el('header',undefined,shell,'top');
    const brand=el('div',undefined,header,'brand');logo(brand);
    const words=el('div',undefined,brand);el('strong','ToxiCheck',words);el('small','Проверка состава продукта',words);
    const close=button(header,'×',()=>{sequence++;viewSequence++;panel.remove();},'close');
    close.setAttribute('aria-label','Закрыть ToxiCheck');
    body=el('div',undefined,shell,'body');
    document.documentElement.append(panel);
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
    const {additives,foods}=P.groups(matched);
    const counts=el('div',undefined,body,'counts');
    for(const [n,label] of [[additives.length,'E-добавки'],[foods.length,'Ингредиенты'],[unmatched.length,'Не распознано']]){
      const c=el('div',undefined,counts,'count');el('strong',String(n),c);el('small',label,c);
    }
    function group(title,items){
      if(!items.length)return;
      el('h3',title+' · '+items.length,body);
      for(const item of items){
        const s=P.status(item.severity,item.rules);
        const card=button(body,'',()=>void showIngredient(item,state),'ingredient '+s.tone);
        if(item.code)el('span',item.code,card,'code');
        const info=el('span',undefined,card,'info');el('strong',item.name,info);badge(info,s);
        const reason=verdict.reasons?.find(r=>r.ingredient_id===item.ingredient_id);
        if(reason?.explanation)el('small',reason.explanation,info);
        el('span','›',card,'arrow').setAttribute('aria-hidden','true');
      }
    }
    group('Пищевые добавки (E-коды)',additives);
    group('Остальные ингредиенты',foods);
    if(unmatched.length){
      const box=el('article',undefined,body,'card');
      el('h4','Не распознано · '+unmatched.length,box);
      el('p','Эти позиции не получили оценку. Проверьте исходный текст.',box,'muted');
      el('p',unmatched.join(', '),box,'unmatched');
    }
    el('small','Оценка основана на каталоге и правилах ToxiCheck. Персональные исключения пока не учитываются. Неполный состав не подтверждает безопасность.',body);
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
    button(body,'← К результатам анализа',()=>showResult(state),'back');
    const title=el('div',undefined,body,'detail-title '+P.status(item.severity,item.rules).tone);
    if(item.code)el('span',item.code,title,'code');
    el('h2',item.name,title);
    const s=P.status(item.severity,item.rules),assessment=detailSection('Оценка');
    badge(assessment,s);el('p',s.description,assessment);
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
  async function scan(){
    if(!allowed())return;
    const {auto}=await chrome.storage.local.get({auto:false});
    if(!auto)return;
    const result=globalThis.ToxiExtract.find(document);
    if(!result)return;
    const key=location.href+'\n'+result.text;
    if(key===lastKey)return;
    lastKey=key;void run(result.text,true);
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(()=>void scan().catch(console.warn),800);}
  chrome.runtime.onMessage.addListener((msg,_sender,respond)=>{
    if(msg?.type==='TOXI_MANUAL'){
      const text=String(msg.text||getSelection()?.toString()||'').trim();
      if(text){void run(text);respond({ok:true});}else respond({ok:false,error:'Выделите текст состава на странице.'});
    }else if(msg?.type==='TOXI_FIND'){
      const found=globalThis.ToxiExtract.find(document);
      if(found){void run(found.text,false,true);respond({ok:true});}else respond({ok:false,error:'Состав не найден. Выделите текст вручную.'});
    }
  });
  if(allowed()){
    const observer=new MutationObserver(records=>{
      if(location.href!==lastUrl){lastUrl=location.href;lastKey='';sequence++;viewSequence++;analysisState=null;panel?.remove();}
      if(records.some(r=>r.target!==panel&&!panel?.contains(r.target)&&!(r.type==='childList'&&[...r.addedNodes,...r.removedNodes].every(n=>n===panel))))schedule();
    });
    observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
    chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.auto){lastKey='';if(changes.auto.newValue)schedule();}});
    schedule();
  }
})();
