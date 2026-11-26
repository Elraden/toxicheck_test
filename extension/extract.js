/* Shared, dependency-free extraction heuristics. No network access. */
(function(root){
  'use strict';
  const normalize = value => String(value ?? '').replace(/\u00a0/g, ' ').replace(/\r/g, '').trim();
  const heading = /^(?:состав(?:\s+(?:продукта|товара))?|ингредиенты|ingredients)\s*[:：]?\s*$/i;
  const start = /(?:^|\n)\s*(?:состав(?:\s+(?:продукта|товара))?|ингредиенты|ingredients)(?:[ \t]*[:：][ \t]*|[ \t]*\n[ \t]*)/i;
  const end = /(?:\n|[.;]\s+)(?:пищевая ценность|энергетическая ценность|условия хранения|срок годности|масса нетто|производитель|способ приготовления|производство|страна производства|характеристики|описание товара|nutrition facts|storage instructions)/i;
  function clean(value){
    const s=normalize(value);
    const marker=s.match(start);
    let body=marker?s.slice(marker.index+marker[0].length):s.replace(/^(?:состав(?:\s+продукта)?|ингредиенты|ingredients)\s*[:：]?\s*\n/i,'');
    const boundary=body.search(end);
    if(boundary>=0)body=body.slice(0,boundary);
    return body.replace(/\s+/g,' ').trim().replace(/[.;]+$/, '').trim().slice(0,20000);
  }
  function plausible(text){
    return text.length>=3 && text.length<=3000 && /[а-яёa-z]/i.test(text) &&
      !heading.test(text) && !/^(?:описание|характеристики|отзывы|доставка|купить)$/i.test(text) && !/^(?:состав не указан|нет информации|не указан|не указано|подробнее|показать ещё|показать еще)$/i.test(text);
  }
  function visible(el){
    return !!el && !el.closest('[hidden],[aria-hidden="true"],script,style,nav,footer') && el.getClientRects().length>0 && getComputedStyle(el).visibility!=='hidden';
  }
  function find(doc){
    const candidates=[];
    const read=el=>normalize(el?.innerText ?? el?.textContent);
    const add=(value,score,method)=>{
      const text=clean(value);
      if(plausible(text))candidates.push({text,score,method});
    };
    for(const el of doc.querySelectorAll('h1,h2,h3,h4,h5,h6,dt,dd,th,span,strong,b,label,p,div,li,td,section,article,summary,button,[role=heading]')){
      if(!visible(el))continue;
      const raw=read(el);
      if(!raw || raw.length>3500)continue;
      if(heading.test(raw)){
        const sibling=el.nextElementSibling;
        if(visible(sibling))add(read(sibling),90,'heading-sibling');
        const controlled=el.getAttribute('aria-controls');
        if(controlled){const target=doc.getElementById(controlled);if(visible(target))add(read(target),94,'aria-controls');}
        // A label may be wrapped in several elements before its value.
        let node=el;
        for(let depth=0;depth<6&&node.parentElement;depth++){
          const parent=node.parentElement;
          if(!visible(parent)||parent.matches('body,main,html'))break;
          if(heading.test(read(node))&&visible(node.nextElementSibling)){
            add(read(node.nextElementSibling),88-depth*3,'wrapped-heading-sibling');
          }
          const whole=read(parent);
          if(whole.length>3500)break;
          if(whole.toLowerCase().startsWith(raw.toLowerCase())&&whole.length>raw.length){
            const rest=whole.slice(raw.length).replace(/^\s*[:：]?\s*/,'');
            if(rest)add(rest,78-depth*3,'heading-ancestor');
            break;
          }
          node=parent;
        }
      }else if(start.test(raw)){
        add(raw,el.childElementCount===0?95:70,'inline-heading');
      }else if(el.childElementCount===0&&/^(?:состав(?:\s+(?:продукта|товара))?|ингредиенты)\s+\S/i.test(raw)){
        add(raw.replace(/^(?:состав(?:\s+(?:продукта|товара))?|ингредиенты)\s+/i,''),65,'inline-no-colon');
      }
    }
    candidates.sort((a,b)=>b.score-a.score||a.text.length-b.text.length);
    return candidates[0]||null;
  }
  root.ToxiExtract={clean,plausible,find};
})(globalThis);
