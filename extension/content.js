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
    shadow=panel.attachShadow({mode:'closed'});
    const style=element('style',undefined,shadow);
    style.textContent=":host{all:initial!important;position:fixed!important;right:16px!important;bottom:16px!important;z-index:2147483647!important;width:min(380px,calc(100vw - 32px))!important;display:block!important;font:400 14px/1.5 system-ui,sans-serif!important;color:#17201d!important;direction:ltr!important;text-align:left!important;writing-mode:horizontal-tb!important;letter-spacing:normal!important;word-spacing:normal!important}\n*,*::before,*::after{box-sizing:border-box}\nsection{display:block;background:#fff;color:#17201d;font:400 14px/1.5 system-ui,sans-serif;letter-spacing:normal;word-spacing:normal;text-align:left;border:1px solid #cbd5d1;border-radius:14px;padding:16px;box-shadow:0 8px 32px #0003;max-height:75vh;overflow:auto}\nheader{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}h2{font:700 20px/1.3 system-ui,sans-serif;margin:0}h3{font:650 15px/1.4 system-ui,sans-serif;margin:14px 0 6px}\nbutton{display:inline-block;font:500 14px/1.4 system-ui,sans-serif;cursor:pointer;padding:8px 12px;border:1px solid #a9b8b1;border-radius:7px;background:white;color:#17201d;white-space:normal}\ntextarea{display:block;width:100%;font:400 14px/1.5 system-ui,sans-serif;color:#17201d;background:white;border:1px solid #a9b8b1;border-radius:7px;padding:8px;min-height:110px;margin:8px 0;resize:vertical;white-space:pre-wrap}\np,li,small{font:400 14px/1.5 system-ui,sans-serif;overflow-wrap:anywhere;white-space:normal;letter-spacing:normal}p{margin:10px 0}ul{display:block;list-style:disc outside;margin:6px 0 12px;padding:0 0 0 22px}li{display:list-item;margin:4px 0}small{display:block;color:#485a51;font-size:12px;margin:5px 0}";
    const section=element('section',undefined,shadow);
    const header=element('header',undefined,section);
    element('h2','ToxiCheck',header);
    const close=element('button','Закрыть',header);
    close.onclick=()=>{sequence++;panel.remove();};
    body=element('div',undefined,section);
    document.documentElement.append(panel);
  }
  async function run(text,automatic=false,detected=automatic){
    open();
    const id=++sequence;
    body.replaceChildren();
    element('small',detected?'Состав найден автоматически. Проверьте, что он полный.':'Состав выбран вручную.',body);
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
      if(found){void run(found.text,false,true);respond({ok:true});}
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
