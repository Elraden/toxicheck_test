const api=document.getElementById('api'),auto=document.getElementById('auto'),status=document.getElementById('status');
const defaults={apiBase:'https://toxicheck.195.209.209.111.sslip.io/api',auto:false};
const save=document.getElementById('save');
save.disabled=true;api.disabled=true;auto.disabled=true;
chrome.storage.local.get(defaults).then(s=>{api.value=s.apiBase;auto.checked=s.auto;save.disabled=false;api.disabled=false;auto.disabled=false;}).catch(e=>{status.textContent=e.message;});
save.onclick=async()=>{
  try{
    const u=new URL(api.value.trim());
    if(u.username||u.password||u.search||u.hash||!(u.protocol==='https:'||u.protocol==='http:'&&['localhost','127.0.0.1'].includes(u.hostname))||!/\/api\/?$/.test(u.pathname))throw Error('Нужен HTTPS-адрес API (или локальный HTTP), заканчивающийся на /api.');
    // Request directly from the click handler only for custom API origins.
    if(u.origin!==new URL(defaults.apiBase).origin && !await chrome.permissions.request({origins:[u.origin+'/*']}))throw Error('Доступ к API не разрешён.');
    const old=await chrome.storage.local.get(defaults);
    await chrome.storage.local.set({apiBase:u.href.replace(/\/$/,''),auto:auto.checked});
    try{const origin=new URL(old.apiBase).origin;if(origin!==u.origin && origin!==new URL(defaults.apiBase).origin)await chrome.permissions.remove({origins:[origin+'/*']});}catch{}
    status.textContent='Настройки сохранены.';
  }catch(e){status.textContent=e.message;}
};
async function invoke(type){
  try{
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab?.id)throw Error('Нет активной вкладки.');
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['extract.js','presentation.js','content.js']});
    const answer=await chrome.tabs.sendMessage(tab.id,{type});
    if(!answer?.ok)throw Error(answer?.error||'Не удалось открыть панель.');
    window.close();
  }catch(e){status.textContent=e.message;}
}
document.getElementById('manual').onclick=()=>void invoke('TOXI_MANUAL');
document.getElementById('find').onclick=()=>void invoke('TOXI_FIND');
