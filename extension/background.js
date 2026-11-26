const MENU='toxicheck-selection';
const defaults={apiBase:'https://toxicheck.195.209.209.111.sslip.io/api',auto:false};
const limits=new Map();
chrome.runtime.onInstalled.addListener(details=>{
  chrome.contextMenus.removeAll(()=>{
    chrome.contextMenus.create({id:MENU,title:'Проверить состав в ToxiCheck',contexts:['selection']});
  });
  // Migrate only the original MVP's localhost default, once on upgrade.
  if(details.reason==='update' && details.previousVersion==='0.1.0'){
    void chrome.storage.local.get('apiBase').then(({apiBase})=>{
      if(apiBase==='http://localhost:8000/api')return chrome.storage.local.set({apiBase:defaults.apiBase,auto:false});
    }).catch(console.error);
  }
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
  const {apiBase,excludedIngredients}=await chrome.storage.local.get({...defaults,excludedIngredients:[]});
  const excludedIngredientIds=Array.isArray(excludedIngredients)?excludedIngredients.map(x=>x.id).filter(x=>typeof x==='string'):[];
  const u=endpoint(apiBase);
  if(!await chrome.permissions.contains({origins:[u.origin+'/*']}))throw Error('Разрешите доступ к API в настройках расширения.');
  let response;
  try{response=await fetch(u.href.replace(/\/$/,'')+'/analysis',{method:'POST',credentials:'omit',redirect:'error',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({ingredientsText:value,source:'label',preferences:{excludedIngredientIds}}),signal:AbortSignal.timeout(15000)});}
  catch(error){throw Error('Не удалось подключиться к API '+u.origin+'. Проверьте адрес и доступность сервера. '+error.message);}
  if(!response.ok)throw Error('API недоступен: HTTP '+response.status);
  const data=await response.json();
  if(!data.verdict||!Array.isArray(data.matched)||!Array.isArray(data.unmatched))throw Error('Неожиданный ответ API.');
  return data;
}
async function ingredientDetail(id){
  if(typeof id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))throw Error('Некорректный идентификатор ингредиента.');
  const {apiBase}=await chrome.storage.local.get(defaults);
  const u=endpoint(apiBase);
  if(!await chrome.permissions.contains({origins:[u.origin+'/*']}))throw Error('Разрешите доступ к API в настройках расширения.');
  const response=await fetch(u.href.replace(/\/$/,'')+'/ingredients/'+encodeURIComponent(id),{credentials:'omit',redirect:'error',headers:{Accept:'application/json'},signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('Не удалось загрузить ингредиент: HTTP '+response.status);
  const data=await response.json();
  if(!data.name||data.id!==id)throw Error('Неожиданный ответ справочника.');
  return data;
}
async function searchCatalog(query,offset){
  const {apiBase}=await chrome.storage.local.get(defaults),u=endpoint(apiBase);
  if(!await chrome.permissions.contains({origins:[u.origin+'/*']}))throw Error('Разрешите доступ к API в настройках расширения.');
  const params=new URLSearchParams({q:String(query||'').slice(0,200),limit:'30',offset:String(Math.max(0,Math.min(100000,Number(offset)||0)))});
  const response=await fetch(u.href.replace(/\/$/,'')+'/ingredients?'+params,{credentials:'omit',redirect:'error',headers:{Accept:'application/json'},signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('Каталог недоступен: HTTP '+response.status);
  const data=await response.json();if(!Array.isArray(data.items))throw Error('Неожиданный ответ каталога.');return data;
}
let preferenceQueue=Promise.resolve();
function setExclusion(item,selected){
  const operation=preferenceQueue.then(async()=>{
    if(!item||typeof item.id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.id))throw Error('Некорректный ингредиент.');
    const {excludedIngredients}=await chrome.storage.local.get({excludedIngredients:[]});
    const list=Array.isArray(excludedIngredients)?excludedIngredients.filter(x=>x.id!==item.id):[];
    if(selected)list.push({id:item.id,name:String(item.name||item.id).slice(0,500),code:String(item.code||'').slice(0,40)});
    await chrome.storage.local.set({excludedIngredients:list});return list;
  });
  preferenceQueue=operation.catch(()=>{});return operation;
}
async function inject(tabId){
  await chrome.scripting.executeScript({target:{tabId},files:['extract.js','presentation.js','content.js']});
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
  if(!['TOXI_ANALYZE','TOXI_INGREDIENT','TOXI_CATALOG','TOXI_EXCLUSION'].includes(msg?.type))return;
  if(sender.id!==chrome.runtime.id||(!sender.tab?.id&&!sender.url?.startsWith(chrome.runtime.getURL('')))){respond({ok:false,error:'Недопустимый источник запроса.'});return;}
  (async()=>{
    if(msg.type==='TOXI_CATALOG'){if(!sender.url?.startsWith(chrome.runtime.getURL('')))throw Error('Недопустимый источник запроса.');return searchCatalog(msg.query,msg.offset);}
    if(msg.type==='TOXI_EXCLUSION')return setExclusion(msg.item,msg.selected===true);
    if(msg.type==='TOXI_INGREDIENT')return ingredientDetail(msg.id);
    if(!sender.tab?.id)throw Error('Нет активной вкладки.');
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
