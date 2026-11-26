/* Presentation mirrors the PWA's regulatory status semantics. */
(function(root){
  const statuses={
    forbidden:{tone:'danger',label:'Запрет применения',description:'В действующих правилах найдены сведения о запрете применения.'},
    avoid:{tone:'danger',label:'Ограничения',description:'Найдены ограничения применения или рекомендации избегать ингредиента.'},
    attention:{tone:'attention',label:'С осторожностью',description:'Необходимо учитывать вид продукта, количество и условия применения.'},
    neutral:{tone:'neutral',label:'Нет предупреждений',description:'По найденным правилам предупреждений нет. Это не гарантия безопасности.'},
    unknown:{tone:'unknown',label:'Нет данных',description:'Недостаточно сведений для обоснованной оценки.'}
  };
  const status=(severity,rules)=>statuses[severity==='neutral'&&Array.isArray(rules)&&!rules.length?'unknown':severity]||statuses.unknown;
  const isAdditive=item=>/^[eе]\s*\d{3,4}[a-zа-я]?(?:\s*\([ivx]+\))?$/i.test(String(item.code||'').trim());
  const weight={danger:0,attention:1,unknown:2,neutral:3};
  function groups(items){
    const sorted=[...items].sort((a,b)=>weight[status(a.severity,a.rules).tone]-weight[status(b.severity,b.rules).tone]);
    return {additives:sorted.filter(isAdditive),foods:sorted.filter(x=>!isAdditive(x))};
  }
  function rulePresentation(rule){
    const c=rule.conditions||{};
    const needsReview=Boolean(c.primary_basis_required||c.production_ready===false||c.verification_status!=='verified_primary');
    const common={needsReview};
    switch(c.regulatory_status){
      case 'PERMITTED_WITH_CONDITIONS':return {...common,title:'Применение с условиями',summary:'Допустимость зависит от вида продукта и количества добавки.'};
      case 'BANNED':return {...common,title:needsReview?'Сведения о запрете':'Запрет применения',summary:needsReview?'Информация требует подтверждения.':'В указанном документе применение добавки запрещено.'};
      case 'PHASE_OUT':return {...common,title:'Изменение условий применения',summary:'Необходимо учитывать дату выпуска и переходные условия.'};
      default:return {...common,title:rule.title||'Условия применения',summary:rule.assessment_note||rule.explanation};
    }
  }
  const sourceUrl=value=>{try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:null;}catch{return null;}};
  const documentLabel=value=>String(value||'').replace(/\bTR_TS_(\d{3})_(\d{4})\b/g,'ТР ТС $1/$2');
  root.ToxiPresentation={status,isAdditive,groups,rulePresentation,sourceUrl,documentLabel};
})(globalThis);
