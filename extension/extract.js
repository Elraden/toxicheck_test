/* Shared, dependency-free extraction heuristics. No network access. */
(function(root){
  'use strict';
  const normalize = value => String(value ?? '').replace(/\u00a0/g, ' ').replace(/\r/g, '').trim();
  const heading = /^(?:состав(?:\s+продукта)?|ингредиенты|ingredients)\s*[:：]?\s*$/i;
  const start = /(?:^|\n)\s*(?:состав(?:\s+продукта)?|ингредиенты|ingredients)\s*[:：]\s*/i;
  const end = /(?:\n|[.;]\s+)(?:пищевая ценность|энергетическая ценность|условия хранения|срок годности|масса нетто|производитель|способ приготовления|nutrition facts|storage instructions)/i;
  function clean(value){
    const s=normalize(value);
    const marker=s.match(start);
    let body=marker?s.slice(marker.index+marker[0].length):s.replace(/^(?:состав(?:\s+продукта)?|ингредиенты|ingredients)\s*[:：]?\s*\n/i,'');
    const boundary=body.search(end);
    if(boundary>=0)body=body.slice(0,boundary);
    return body.replace(/\s+/g,' ').trim().slice(0,20000);
  }
  function plausible(text){
    return text.length>=8 && text.length<=3000 && /[а-яёa-z]/i.test(text) &&
      !heading.test(text) && !/^(?:состав не указан|нет информации|не указан|не указано|подробнее|показать ещё|показать еще)$/i.test(text);
  }
  function visible(el){
    return !!el && !el.closest('[hidden],[aria-hidden="true"],script,style,nav,footer,header') && el.getClientRects().length>0 && getComputedStyle(el).visibility!=='hidden';
  }
  function find(doc){
    const candidates=[];
    const add=(value,score,method)=>{
      const text=clean(value);
      if(plausible(text))candidates.push({text,score,method});
    };
    for(const el of doc.querySelectorAll('h1,h2,h3,h4,h5,dt,th,span,strong,b,label,p,div,li,td')){
      if(!visible(el))continue;
      const raw=normalize(el.innerText);
      if(!raw || raw.length>3500)continue;
      if(heading.test(raw)){
        const sibling=el.nextElementSibling;
        if(visible(sibling))add(sibling.innerText,90,'heading-sibling');
        const parent=el.parentElement;
        if(parent && parent.childElementCount<=5 && visible(parent)){
          const body=normalize(parent.innerText).slice(raw.length).trim();
          if(body)add(body,80,'heading-parent');
        }
      }else if(start.test(raw)){
        add(raw,el.childElementCount===0?95:70,'inline-heading');
      }
    }
    candidates.sort((a,b)=>b.score-a.score||a.text.length-b.text.length);
    return candidates[0]||null;
  }
  root.ToxiExtract={clean,plausible,find};
})(globalThis);
