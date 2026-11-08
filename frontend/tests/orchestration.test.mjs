import test from 'node:test';
import assert from 'node:assert/strict';
import { Orchestrator, defaults, modes, validateConfig } from '../src/research/orchestration/engine.ts';

class Clock {
  time=0;items=[];next=0;
  now=()=>this.time;
  after=(ms,fn)=>{const item={at:this.time+ms,id:this.next++,fn,live:true};this.items.push(item);return ()=>{item.live=false;};};
  async advance(to){
    for(;;){
      await Promise.resolve();await Promise.resolve();
      this.items.sort((a,b)=>a.at-b.at||a.id-b.id);
      const item=this.items.find(x=>x.live&&x.at<=to);if(!item)break;
      item.live=false;this.time=item.at;item.fn();
    }
    this.time=to;await Promise.resolve();await Promise.resolve();
  }
}
function setup(mode,lookup=()=>new Promise(()=>{}),ocr=()=>new Promise(()=>{})){
  const clock=new Clock(),events=[],finishes=[],calls={ocr:0,cancel:0,lookup:0};
  const engine=new Orchestrator(mode,defaults,{clock,emit:e=>events.push(e),
    lookup:code=>{calls.lookup++;return lookup(code,clock);},ocr:img=>{calls.ocr++;return ocr(img,clock);},
    cancel:()=>calls.cancel++,stopBarcode:()=>{},changed:()=>{},finished:v=>finishes.push(v)});
  engine.start();engine.ready();
  return {clock,events,finishes,calls,engine};
}
const answer=(kind,sufficient)=>({id:kind+'-id',kind,status:'finished',sufficient,result:{},metrics:{}});
test('exactly five modes, configuration rejects invalid thresholds',()=>{
  assert.equal(modes.length,5);assert.ok(!modes.includes('photo_parallel'));
  for(const c of [{...defaults,photoHintMs:4000},{...defaults,maxOcrRequests:4},{...defaults,maxOcrInFlight:2},{...defaults,runMaxMs:NaN}])
    assert.throws(()=>validateConfig(c));
});
test('known missing ingredients immediately enables fallback in all modes',async()=>{
  for(const mode of modes){
    const s=setup(mode,async()=>answer('lookup',false));await s.clock.advance(100);s.engine.detected('4600000000001');
    await s.clock.advance(100);
    assert.equal(s.engine.ocrAllowed,true);
    if(mode.startsWith('photo'))assert.equal(s.events.find(e=>e.event_name==='photo_hint_shown').relative_ms,100);
  }
});
test('photo adaptive hint does not interrupt barcode and needs photo confirmation',async()=>{
  const s=setup('photo_adaptive');await s.clock.advance(1500);
  assert.equal(s.engine.photoHint,true);assert.equal(s.engine.barcodeState,'running');
  assert.equal(s.engine.submit('image'),false);s.engine.selectPhoto();
  assert.equal(s.engine.submit('image'),false);assert.equal(s.engine.submit('image',true),true);
  assert.equal(s.calls.ocr,1);
});
test('video parallel allows immediately; adaptive only at hedge; sequential only on failure',async()=>{
  for(const mode of ['video_parallel','video_adaptive','video_sequential']){
    const s=setup(mode);assert.equal(s.engine.submit('frame'),mode==='video_parallel');
    await s.clock.advance(1499);assert.equal(s.calls.ocr,mode==='video_parallel'?1:0);
    await s.clock.advance(1500);assert.equal(s.engine.ocrAllowed,mode!=='video_sequential');
    if(mode==='video_sequential'){await s.clock.advance(4000);assert.equal(s.engine.ocrAllowed,true);}
  }
});
test('fast sufficient lookup wins once and prevents late OCR; decoding alone does not win',async()=>{
  let resolve;const s=setup('video_adaptive',()=>new Promise(r=>{resolve=r;}));
  s.engine.detected('4600000000001');assert.equal(s.finishes.length,0);
  resolve(answer('lookup',true));await s.clock.advance(100);
  assert.equal(s.finishes.length,1);assert.equal(s.finishes[0].outcome,'valid_barcode');
  await s.clock.advance(30000);assert.equal(s.calls.ocr,0);assert.equal(s.finishes.length,1);
  assert.equal(s.clock.items.filter(i=>i.live).length,0);
});
test('same-time ready lookup has precedence over hedge activation',async()=>{
  const s=setup('video_adaptive',(_,clock)=>new Promise(r=>clock.after(1500,()=>r(answer('lookup',true)))));
  s.engine.detected('4600000000001');await s.clock.advance(1500);
  assert.equal(s.finishes[0].outcome,'valid_barcode');assert.equal(s.engine.ocrAllowed,false);
});
test('one in-flight OCR, bounded retries, late OCR cannot change winner',async()=>{
  let finishOCR;const s=setup('video_parallel',async()=>answer('lookup',true),()=>new Promise(r=>{finishOCR=r;}));
  assert.equal(s.engine.submit('x'),true);assert.equal(s.engine.submit('y'),false);
  s.engine.detected('4600000000001');await s.clock.advance(1);finishOCR(answer('ocr',true));await s.clock.advance(2);
  assert.equal(s.finishes.length,1);assert.equal(s.finishes[0].outcome,'valid_barcode');
});
test('insufficient OCR never wins; exhausted branches finish without waiting deadline',async()=>{
  const s=setup('video_sequential',undefined,async()=>answer('ocr',false));
  s.engine.barcodeFailed();
  for(let i=0;i<3;i++){await s.clock.advance(i*1500);s.engine.submit('image');await s.clock.advance(i*1500);}
  assert.equal(s.finishes[0].outcome,'incomplete');assert.equal(s.finishes[0].summary.elapsed_ms,3000);
});
test('no camera means no frames, global timeout still terminates',async()=>{
  const s=setup('video_parallel');s.engine.camera=false;assert.equal(s.engine.submit('x'),false);
  await s.clock.advance(30000);assert.equal(s.finishes[0].outcome,'timeout');assert.equal(s.calls.ocr,0);
});
test('observer markers never trigger OCR or alter state',()=>{
  const s=setup('video_sequential');s.engine.event('composition_visible');s.engine.event('package_turned');
  assert.equal(s.engine.ocrAllowed,false);assert.equal(s.calls.ocr,0);
});
test('identical inputs yield reproducible timing and event sequence',async()=>{
  const perform=async()=>{const s=setup('video_adaptive',async()=>answer('lookup',false),async()=>answer('ocr',true));
    await s.clock.advance(200);s.engine.detected('4600000000001');await s.clock.advance(200);s.engine.submit('x');await s.clock.advance(300);return s.events;};
  assert.deepEqual(await perform(),await perform());
});
