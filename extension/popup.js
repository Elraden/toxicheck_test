const auto=document.getElementById('auto'),status=document.getElementById('status');
chrome.storage.local.get({auto:false}).then(s=>{auto.checked=s.auto;auto.disabled=false;}).catch(e=>status.textContent=e.message);
auto.onchange=async()=>{
 const enabled=auto.checked;auto.disabled=true;
 try{
  await chrome.storage.local.set({auto:enabled});
  status.textContent=enabled?'Автоматический анализ включён.':'Автоматический анализ отключён.';
  if(enabled){
   const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
   if(tab?.id){
    try{
     await chrome.scripting.executeScript({target:{tabId:tab.id},files:['extract.js','presentation.js','content.js']});
     const result=await chrome.tabs.sendMessage(tab.id,{type:'TOXI_AUTO_SCAN'});
     if(result?.message)status.textContent=result.message;
    }catch{status.textContent='Автоматический режим включён. Откройте или обновите карточку товара.';}
   }
  }
 }catch(e){auto.checked=!enabled;status.textContent=e.message;}finally{auto.disabled=false;}
};
document.getElementById('settings').onclick=()=>chrome.runtime.openOptionsPage();
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
