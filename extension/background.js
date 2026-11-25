const MENU='toxicheck-selection';
const defaults={apiBase:'http://localhost:8000/api',auto:false};
const limits=new Map();
chrome.runtime.onInstalled.addListener(()=>{
  chrome.contextMenus.create({id:MENU,title:'Проверить состав в ToxiCheck',contexts:['selection']});
});
function isAllowedPage(url){
  try{const h=new URL(url).hostname;return ['ozon.ru','wildberries.ru','lavka.yandex.ru'].some(d=>h===d||h.endsWith('.'+d));}catch{return false;}
}
function endpoint(raw){
  const u=new URL(raw);
  if(u.username||u.password||u.search||u.hash||!(u.protocol==='https:'||u.protocol==='http:'&&['localhost','127.0.0.1'].includes(u.hostname)))throw Error('Укажите HTTPS-адрес API или локальный HTTP.');
  if(!/\/api\/?$/.test(u.pathname))throw Error('Адрес API должен заканчиваться на /api.');
  return u;
}
async function analyze(text){
  const value=String(text??'').trim();
  if(!value||value.length>20000)throw Error('Состав должен содержать от 1 до 20 000 символов.');
  const {apiBase}=await chrome.storage.local.get(defaults);
  const u=endpoint(apiBase);
  if(!await chrome.permissions.contains({origins:[u.origin+'/*']}))throw Error('Разрешите доступ к API в настройках расширения.');
  const response=await fetch(u.href.replace(/\/$/,'')+'/analysis',{method:'POST',credentials:'omit',redirect:'error',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({ingredientsText:value,source:'label',preferences:{excludedIngredientIds:[]}}),signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('API недоступен: HTTP '+response.status);
  const data=await response.json();
  if(!data.verdict||!Array.isArray(data.matched)||!Array.isArray(data.unmatched))throw Error('Неожиданный ответ API.');
  return data;
}
async function inject(tabId){
  await chrome.scripting.executeScript({target:{tabId},files:['extract.js','content.js']});
}
async function showSelection(info,tab){
  if(!tab?.id||!info.selectionText)return;
  try{
    await inject(tab.id);
    await chrome.tabs.sendMessage(tab.id,{type:'TOXI_MANUAL',text:info.selectionText});
  }catch(e){console.warn('ToxiCheck: cannot open panel',e.message);}
}
chrome.contextMenus.onClicked.addListener((info,tab)=>{if(info.menuItemId===MENU)void showSelection(info,tab);});
chrome.runtime.onMessage.addListener((msg,sender,respond)=>{
  if(msg?.type!=='TOXI_ANALYZE')return;
  if(sender.id!==chrome.runtime.id||!sender.tab?.id){respond({ok:false,error:'Недопустимый источник запроса.'});return;}
  (async()=>{
    const tabId=sender.tab.id;
    if(msg.automatic){
      const {auto}=await chrome.storage.local.get(defaults);
      if(!auto||!isAllowedPage(sender.url))throw Error('Автоматический режим отключён.');
    }
    const now=Date.now();
    if(now-(limits.get(tabId)||0)<1500)throw Error('Повторите запрос через несколько секунд.');
    limits.set(tabId,now);
    return analyze(msg.text);
  })().then(data=>respond({ok:true,data}),e=>respond({ok:false,error:e.message}));
  return true;
});
chrome.tabs.onRemoved.addListener(id=>limits.delete(id));
