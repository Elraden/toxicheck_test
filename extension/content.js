(() => {
  if(globalThis.__toxiInstalled)return;
  globalThis.__toxiInstalled=true;
  const allowed=()=>['ozon.ru','wildberries.ru','lavka.yandex.ru'].some(d=>location.hostname===d||location.hostname.endsWith('.'+d));
  let panel,shadow,body,sequence=0,lastKey='',timer,lastUrl=location.href;
  function element(tag,text,parent){
    const el=document.createElement(tag);
    if(text!==undefined)el.textContent=text;
    if(parent)parent.append(el);
    return el;
  }
  function open(){
    if(panel?.isConnected)return;
    panel=document.createElement('div');
    panel.style.cssText='position:fixed;right:16px;bottom:16px;z-index:2147483647;width:min(360px,calc(100vw - 32px));';
    shadow=panel.attachShadow({mode:'closed'});
    const style=element('style',undefined,shadow);
    style.textContent=':host{font:14px system-ui;color:#17201d}section{background:white;border:1px solid #cbd5d1;border-radius:14px;padding:16px;box-shadow:0 8px 32px #0003;max-height:70vh;overflow:auto}h2{font-size:18px;margin:0 0 12px}button{font:inherit;cursor:pointer;padding:7px 10px;border:1px solid #a9b8b1;border-radius:6px;background:white;color:#17201d}textarea{box-sizing:border-box;width:100%;font:inherit;min-height:85px;margin:8px 0}p,li{overflow-wrap:anywhere}ul{padding-left:20px}small{color:#485a51}header{display:flex;justify-content:space-between;align-items:center}';
    const section=element('section',undefined,shadow);
    const header=element('header',undefined,section);
    element('h2','ToxiCheck',header);
    const close=element('button','Закрыть',header);
    close.onclick=()=>{sequence++;panel.remove();};
    body=element('div',undefined,section);
    document.documentElement.append(panel);
  }
  async function run(text,automatic=false){
    open();
    const id=++sequence;
    body.replaceChildren();
    element('small',automatic?'Состав найден автоматически. Проверьте, что он полный.':'Состав выбран вручную.',body);
    const field=element('textarea',undefined,body);field.value=text;
    field.setAttribute('aria-label','Текст состава');
    const button=element('button','Проверить состав',body);
    button.onclick=()=>void run(field.value,false);
    const status=element('p','Выполняется анализ…',body);
    try{
      const result=await chrome.runtime.sendMessage({type:'TOXI_ANALYZE',text,automatic});
      if(id!==sequence)return;
      if(!result?.ok)throw Error(result?.error||'Сервис недоступен.');
      const {verdict,matched,unmatched}=result.data;
      status.textContent=verdict.title;
      element('p',verdict.description,body);
      element('small','Анализ по указанному тексту, без персональных исключений. Неполный состав не подтверждает безопасность.',body);
      element('h3','Распознано: '+matched.length,body);
      const list=element('ul',undefined,body);
      for(const item of matched)element('li',[item.code,item.name,item.severity].filter(Boolean).join(' · '),list);
      if(unmatched.length){element('h3','Не распознано: '+unmatched.length,body);element('p',unmatched.join(', '),body);}
    }catch(e){if(id===sequence)status.textContent=e.message;}
  }
  async function scan(){
    if(!allowed())return;
    const {auto}=await chrome.storage.local.get({auto:false});
    if(!auto)return;
    const result=globalThis.ToxiExtract.find(document);
    if(!result)return;
    const key=location.href+'\n'+result.text;
    if(key===lastKey)return;
    lastKey=key;
    void run(result.text,true);
  }
  function schedule(){
    clearTimeout(timer);
    timer=setTimeout(()=>void scan().catch(console.warn),800);
  }
  chrome.runtime.onMessage.addListener((msg,_sender,respond)=>{
    if(msg?.type==='TOXI_MANUAL'){
      const text=String(msg.text||getSelection()?.toString()||'').trim();
      if(text){void run(text);respond({ok:true});}
      else{respond({ok:false,error:'Выделите текст состава на странице.'});}
    }else if(msg?.type==='TOXI_FIND'){
      const found=globalThis.ToxiExtract.find(document);
      if(found){void run(found.text);respond({ok:true});}
      else respond({ok:false,error:'Состав не найден. Выделите текст вручную.'});
    }
  });
  if(allowed()){
    const observer=new MutationObserver(records=>{
      if(location.href!==lastUrl){lastUrl=location.href;lastKey='';sequence++;panel?.remove();}
      if(records.some(r=>r.target!==panel&&!panel?.contains(r.target)&&!(r.type==='childList'&&[...r.addedNodes,...r.removedNodes].every(n=>n===panel))))schedule();
    });
    observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
    chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.auto){lastKey='';if(changes.auto.newValue)schedule();}});
    schedule();
  }
})();
