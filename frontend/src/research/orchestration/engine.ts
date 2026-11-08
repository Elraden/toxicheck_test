export const modes = ['photo_sequential','photo_adaptive','video_sequential','video_parallel','video_adaptive'] as const;
export type Mode = typeof modes[number];
export type Config = { barcodeMaxMs:number; photoHintMs:number; ocrHedgeMs:number; lookupMaxMs:number; runMaxMs:number; frameMinIntervalMs:number; maxOcrRequests:number; maxOcrInFlight:1 };
export const defaults:Config = { barcodeMaxMs:4000,photoHintMs:1500,ocrHedgeMs:1500,lookupMaxMs:8000,runMaxMs:30000,frameMinIntervalMs:1500,maxOcrRequests:3,maxOcrInFlight:1 };
export type Event = { seq:number; event_name:string; relative_ms:number; details:Record<string,unknown> };
export type Task = { id:string; kind:'ocr'|'lookup'; status:string; sufficient:boolean; result:Record<string,unknown>|null; metrics:Record<string,unknown>; cancellation?:Record<string,unknown> };
export type Finish = { outcome:string; winning_task_id:string|null; summary:Record<string,unknown> };
export interface Clock { now():number; after(ms:number,fn:()=>void):()=>void }
export const realClock:Clock = { now:()=>performance.now(), after:(ms,fn)=>{const id=setTimeout(fn,ms);return ()=>clearTimeout(id);} };
export interface Ports {
  clock:Clock;
  emit:(event:Event)=>void;
  lookup:(code:string)=>Promise<Task>;
  ocr:(image:string,metrics?:Record<string,unknown>)=>Promise<Task>;
  cancel:()=>void;
  stopBarcode:()=>void;
  cancelLookup?:()=>void;
  changed:()=>void;
  finished:(result:Finish,task?:Task)=>void;
}
export function validateConfig(c:Config) {
  if (!Object.values(c).every(Number.isFinite) || !(c.photoHintMs>=0 && c.photoHintMs<c.barcodeMaxMs &&
    c.ocrHedgeMs>=0 && c.ocrHedgeMs<c.barcodeMaxMs && c.barcodeMaxMs>=500 && c.barcodeMaxMs<=15000 &&
    c.lookupMaxMs>=500 && c.lookupMaxMs<=15000 && c.lookupMaxMs<c.runMaxMs &&
    c.barcodeMaxMs<c.runMaxMs && c.runMaxMs>=10000 && c.runMaxMs<=120000 &&
    c.frameMinIntervalMs>=1000 && c.frameMinIntervalMs<=10000 &&
    Number.isInteger(c.maxOcrRequests) && c.maxOcrRequests>=1 && c.maxOcrRequests<=3 && c.maxOcrInFlight===1))
    throw new Error('Проверьте пороги и ограничения: подсказка и ранний OCR должны предшествовать тайм-ауту штрихкода.');
}
export class Orchestrator {
  mode:Mode; config:Config; ports:Ports; started:number;
  done=false; camera=false; barcodeState='waiting'; lookupState='idle'; ocrState='idle';
  photoHint=false; photoSelected=false; ocrAllowed=false; ocrCalls=0;
  events:Event[]=[]; timers=new Set<()=>void>(); barcodeTimer?:()=>void; lookupTimer?:()=>void;
  lastRequest=-Infinity; winner:Task|undefined;
  constructor(mode:Mode,config:Config,ports:Ports,started=ports.clock.now()) {
    if (!modes.includes(mode)) throw new Error('Неизвестный режим.');
    validateConfig(config); this.mode=mode; this.config={...config}; this.ports=ports; this.started=started;
  }
  event(name:string,details:Record<string,unknown>={}) {
    const event={seq:this.events.length,event_name:name,relative_ms:name==='run_started'?0:Math.max(0,this.ports.clock.now()-this.started),details};
    this.events.push(event); this.ports.emit(event);
  }
  timer(ms:number,fn:()=>void) {
    const cancel=this.ports.clock.after(Math.max(0,ms),()=>{this.timers.delete(cancel);if(!this.done)fn();});
    this.timers.add(cancel); return cancel;
  }
  start() {
    this.event('run_started',{mode:this.mode});
    this.timer(this.config.runMaxMs-(this.ports.clock.now()-this.started),()=>{this.event('run_timeout');this.finish('timeout');});
    if(this.mode==='video_parallel')this.ocrAllowed=true;
    if(this.mode==='photo_adaptive'||this.mode==='video_adaptive'){
      const threshold=this.mode==='photo_adaptive'?this.config.photoHintMs:this.config.ocrHedgeMs;
      // Same-tick ready promise results get priority over speculative activation.
      this.timer(threshold-(this.ports.clock.now()-this.started),()=>{this.timer(0,()=>this.allowOcr());});
    }
    this.ports.changed();
  }
  ready() {
    if(this.done)return;
    this.camera=true; this.event('camera_ready');
    if(!this.photoSelected){
      this.barcodeState='running'; this.event('barcode_started');
      this.barcodeTimer=this.timer(this.config.barcodeMaxMs,()=>this.barcodeFailed('barcode_timeout'));
    }
    this.ports.changed();
  }
  barcodeFailed(reason='barcode_error') {
    if(this.done || this.barcodeState==='detected' || this.barcodeState==='failed')return;
    this.barcodeState='failed';this.barcodeTimer?.();this.ports.stopBarcode();this.event(reason);
    this.allowOcr();this.exhausted();
  }
  detected(code:string) {
    if(this.done || this.barcodeState!=='running')return;
    this.barcodeState='detected';this.barcodeTimer?.();this.ports.stopBarcode();
    this.event('barcode_detected',{code});this.lookupState='running';this.event('lookup_started',{code});
    this.lookupTimer=this.timer(this.config.lookupMaxMs,()=>{
      this.lookupState='timeout';this.event('lookup_timeout');this.ports.cancelLookup?.();this.allowOcr();this.exhausted();
    });
    void this.ports.lookup(code).then(task=>{
      if(this.done||this.lookupState!=='running')return;
      this.lookupTimer?.();
      this.lookupState=task.sufficient?'sufficient':'insufficient';
      this.event(task.sufficient?'lookup_sufficient':'lookup_insufficient',{task_id:task.id,metrics:task.metrics});
      if(task.sufficient)this.finish('valid_barcode',task);
      else {this.allowOcr();this.exhausted();}
    }).catch(error=>{
      if(this.done||this.lookupState!=='running')return;
      this.lookupTimer?.();this.lookupState='error';this.event('lookup_error',{message:String(error)});
      this.allowOcr();this.exhausted();
    });
    this.ports.changed();
  }
  allowOcr() {
    if(this.done)return;
    this.ocrAllowed=true;
    if(this.mode.startsWith('photo')&&!this.photoHint){
      this.photoHint=true; this.event('photo_hint_shown');
    }
    this.ports.changed();
  }
  selectPhoto() {
    if(this.done || !this.photoHint || !this.camera)return;
    this.photoSelected=true;this.event('photo_selected');
    if(this.barcodeState==='running'){
      this.barcodeState='stopped_for_photo';this.barcodeTimer?.();this.ports.stopBarcode();
    }
    this.ports.changed();
  }
  canCapture() {
    return !this.done && this.camera && this.ocrAllowed && this.ocrState!=='running' &&
      this.ocrCalls<this.config.maxOcrRequests && this.ports.clock.now()-this.lastRequest>=this.config.frameMinIntervalMs;
  }
  submit(image:string,confirmed=false,metrics:Record<string,unknown>={}) {
    if(!this.canCapture() || (this.mode.startsWith('photo')&&(!confirmed||!this.photoSelected)))return false;
    this.event(this.mode.startsWith('photo')?'photo_ready':'frame_selected',{attempt:this.ocrCalls+1,...metrics});
    this.ocrCalls++;this.lastRequest=this.ports.clock.now();this.ocrState='running';
    this.event('ocr_request_started',{attempt:this.ocrCalls});
    void this.ports.ocr(image,metrics).then(task=>{
      if(this.done)return;
      this.ocrState=task.sufficient?'sufficient':'insufficient';
      this.event('ocr_http_finished',{task_id:task.id,status:task.status,metrics:task.metrics});
      if(task.sufficient)this.finish('valid_ocr',task);
      else {this.exhausted();this.ports.changed();}
    }).catch(error=>{
      if(this.done)return;
      this.ocrState='error';this.event('ocr_error',{message:String(error)});this.exhausted();this.ports.changed();
    });
    this.ports.changed();return true;
  }
  exhausted() {
    if(this.ocrCalls>=this.config.maxOcrRequests && this.ocrState!=='running' &&
      this.barcodeState!=='running' && this.lookupState!=='running')this.finish('incomplete');
  }
  finish(outcome:string,task?:Task) {
    if(this.done)return;
    this.done=true;this.winner=task;this.photoHint=false;
    if(task)this.event('result_validated',{task_id:task.id,outcome});
    this.event('run_finished',{outcome});
    for(const cancel of this.timers)cancel(); this.timers.clear();
    this.ports.stopBarcode();this.ports.cancel();this.ports.changed();
    this.ports.finished({outcome,winning_task_id:task?.id??null,summary:{
      elapsed_ms:this.ports.clock.now()-this.started,ocr_calls:this.ocrCalls,client_cpu:'not_measured',client_ram:'not_measured'
    }},task);
  }
}
