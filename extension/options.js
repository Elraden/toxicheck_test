const defaults={apiBase:'https://toxicheck.195.209.209.111.sslip.io/api'};
const api=document.getElementById('api'),status=document.getElementById('status'),save=document.getElementById('save'),reset=document.getElementById('reset');
chrome.storage.local.get(defaults).then(s=>{api.value=s.apiBase;save.disabled=false;reset.disabled=false;}).catch(e=>status.textContent=e.message);
async function store(raw){
 try{
  const u=new URL(raw.trim());
  if(u.username||u.password||u.search||u.hash||!(u.protocol==='https:'||u.protocol==='http:'&&['localhost','127.0.0.1'].includes(u.hostname))||!/\/api\/?$/.test(u.pathname))throw Error('Укажите HTTPS-адрес API (или локальный HTTP) с /api.');
  if(u.origin!==new URL(defaults.apiBase).origin&&!await chrome.permissions.request({origins:[u.origin+'/*']}))throw Error('Доступ не разрешён.');
  const old=await chrome.storage.local.get(defaults);
  const next=u.href.replace(/\/$/,'');
  await chrome.storage.local.set({apiBase:next});api.value=next;
  try{const origin=new URL(old.apiBase).origin;if(origin!==u.origin&&origin!==new URL(defaults.apiBase).origin)await chrome.permissions.remove({origins:[origin+'/*']});}catch{}
  status.textContent='Настройки сохранены.';
 }catch(e){status.textContent=e.message;}
}
save.onclick=()=>void store(api.value);
reset.onclick=()=>void store(defaults.apiBase);
