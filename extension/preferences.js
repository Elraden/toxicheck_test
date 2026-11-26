const $=id=>document.getElementById(id);
let selected=[],items=[],offset=0,total=0,revision=0,timer,busy=false;
function el(tag,text,parent,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;if(parent)parent.append(node);return node;}
async function message(data){const r=await chrome.runtime.sendMessage(data);if(!r?.ok)throw Error(r?.error||'Не удалось выполнить запрос.');return r.data;}
function render(){
 $('count').textContent='('+selected.length+')';$('selected').replaceChildren();$('catalog').replaceChildren();
 if(!selected.length)el('p','Список исключений пока пуст.',$('selected'),'note');
 for(const item of selected){const b=el('button',[item.code,item.name].filter(Boolean).join(' ')+' · Убрать',$('selected'),'excluded');b.onclick=()=>void change(item,false);}
 for(const item of items){
   const row=el('div',undefined,$('catalog'),'catalog-row');const text=el('div',undefined,row);el('strong',[item.code,item.name].filter(Boolean).join(' '),text);
   if(item.category)el('small',item.category,text,'note');
   const checked=selected.some(x=>x.id===item.id);const b=el('button',checked?'Убрать':'Исключить',row,checked?'excluded':'');b.onclick=()=>void change(item,!checked);b.setAttribute('aria-pressed',String(checked));
 }
 $('more').hidden=offset>=total;$('more').disabled=busy;
}
async function change(item,checked){try{selected=await message({type:'TOXI_EXCLUSION',item,selected:checked});render();$('status').textContent='Предпочтения сохранены.';}catch(e){$('status').textContent=e.message;}}
async function load(reset){
 const id=reset?++revision:revision;if(!reset&&busy)return;if(reset){offset=0;items=[];total=0;}
 busy=true;render();$('status').textContent='Загружаем каталог…';
 try{
  const data=await message({type:'TOXI_CATALOG',query:$('query').value,offset});
  if(id!==revision)return;
  items.push(...data.items);offset+=data.items.length;total=data.total;
  $('status').textContent=items.length?'':'Ингредиенты не найдены.';
 }catch(e){if(id===revision)$('status').textContent=e.message;}
 finally{if(id===revision){busy=false;render();}}
}
$('query').oninput=()=>{clearTimeout(timer);revision++;busy=false;timer=setTimeout(()=>void load(true),300);};
$('more').onclick=()=>void load(false);
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.excludedIngredients){selected=changes.excludedIngredients.newValue||[];render();}});
(async()=>{try{selected=(await chrome.storage.local.get({excludedIngredients:[]})).excludedIngredients;render();await load(true);}catch(e){$('status').textContent=e.message;}})();
