const test=require('node:test');
const assert=require('node:assert/strict');
require('../presentation.js');
const P=globalThis.ToxiPresentation;
test('regulatory statuses remain distinct and neutral without evidence is unknown',()=>{
 assert.equal(P.status('forbidden',[]).tone,'danger');
 assert.equal(P.status('attention',[]).tone,'attention');
 assert.equal(P.status('neutral',[]).tone,'unknown');
 assert.equal(P.status('neutral',[{}]).label,'Нет предупреждений');
 assert.equal(P.status('unexpected',[]).tone,'unknown');
});
test('E codes are separate and restrictions appear first',()=>{
 const items=[{code:'E330',severity:'neutral',rules:[{}]},{code:null,severity:'unknown'},{code:'E250',severity:'attention'},{code:'E171',severity:'forbidden'}];
 const g=P.groups(items);
 assert.deepEqual(g.additives.map(x=>x.code),['E171','E250','E330']);
 assert.equal(g.foods.length,1);
 assert.equal(P.isAdditive({code:'E160a(ii)'}),true);
});
test('unverified bans are not presented as confirmed',()=>{
 const r=P.rulePresentation({conditions:{regulatory_status:'BANNED',verification_status:'pending'}});
 assert.equal(r.title,'Сведения о запрете');assert.equal(r.needsReview,true);
});
test('source links allow http and https only',()=>{
 assert.equal(P.sourceUrl('javascript:alert(1)'),null);
 assert.equal(P.sourceUrl('https://example.org/doc'),'https://example.org/doc');
});
