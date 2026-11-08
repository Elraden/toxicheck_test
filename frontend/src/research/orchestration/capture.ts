// Camera-side technical frame assessment. No text/ingredient recognition runs here.
export type FrameState={previous:number[]|null;lastSent:number[]|null;geometry?:string};
export type FrameQuality={
  mean:number;brightness:number;contrast:number;edge:number;sharpness:number;
  movement:number|null;width:number;height:number;capture_ms:number;rotation:number;
};
export type FrameAssessment=FrameQuality&{accepted:boolean;reason:string|null};
export type CapturedFrame={original:HTMLCanvasElement;image:string;fingerprint:number[];quality:FrameQuality};
export function resetFrames(state:FrameState){state.previous=null;state.lastSent=null;state.geometry=undefined;}
export function quarterTurn(angle:number){return ((angle%360)+360)%360;}
export function previewRotation(width:number,height:number,layout:'portrait'|'landscape',manualAngle=0){
  const base=width>0&&height>0&&width!==height&&((width>height)!==(layout==='landscape'))?90:0;
  return quarterTurn(base+manualAngle);
}
export function rotatedSize(width:number,height:number,angle:number){
  const a=quarterTurn(angle);
  if(![0,90,180,270].includes(a))throw new Error('Only quarter turns are supported');
  return a===90||a===270?{width:height,height:width}:{width,height};
}
export function rotateCanvas(source:HTMLCanvasElement,angle:number){
  const canvas=document.createElement('canvas'),size=rotatedSize(source.width,source.height,angle);
  canvas.width=size.width;canvas.height=size.height;
  const ctx=canvas.getContext('2d');
  if(!ctx)throw new Error('Canvas недоступен');
  ctx.translate(canvas.width/2,canvas.height/2);
  ctx.rotate(quarterTurn(angle)*Math.PI/180);
  ctx.drawImage(source,-source.width/2,-source.height/2);
  return canvas;
}
export function prepareFrameState(state:FrameState,width:number,height:number,angle:number){
  const geometry=width+'x'+height+':'+quarterTurn(angle);
  if(state.geometry!==geometry){resetFrames(state);state.geometry=geometry;}
}
export function photoImage(original:HTMLCanvasElement,angle:number){
  return rotateCanvas(original,angle).toDataURL('image/jpeg',.9);
}
function sharpness(gray:number[],width:number,height:number):number{
  // Variance of the discrete Laplacian: inexpensive relative focus score.
  let sum=0,sumSquares=0,count=0;
  for(let y=1;y<height-1;y++)for(let x=1;x<width-1;x++){
    const i=y*width+x,v=gray[i-1]!+gray[i+1]!+gray[i-width]!+gray[i+width]!-4*gray[i]!;
    sum+=v;sumSquares+=v*v;count++;
  }
  return count?Math.max(0,sumSquares/count-(sum/count)**2):0;
}
export function frameScore(q:FrameQuality):number{
  // For ranking nearby candidates, not an OCR confidence or an absolute threshold.
  return Math.log1p(q.sharpness)*2+q.contrast/10-Math.abs(q.brightness-128)/35-(q.movement??0)/15;
}
export function capture(video:HTMLVideoElement,state:FrameState,automatic:boolean,angle=0,
  observe?:(quality:FrameAssessment)=>void):CapturedFrame|null{
  const started=performance.now(),rotation=quarterTurn(angle);
  const missing=(reason:string)=>{
    observe?.({accepted:false,reason,mean:0,brightness:0,contrast:0,edge:0,sharpness:0,
      movement:null,width:0,height:0,capture_ms:performance.now()-started,rotation});
    return null;
  };
  if(video.readyState<2||Math.max(video.videoWidth,video.videoHeight)<480||
    Math.min(video.videoWidth,video.videoHeight)<320)return missing('camera_not_ready');
  prepareFrameState(state,video.videoWidth,video.videoHeight,angle);
  const original=document.createElement('canvas');
  const scale=Math.min(1,1600/Math.max(video.videoWidth,video.videoHeight));
  original.width=Math.round(video.videoWidth*scale);original.height=Math.round(video.videoHeight*scale);
  const ctx=original.getContext('2d');if(!ctx)return missing('canvas_unavailable');
  ctx.drawImage(video,0,0,original.width,original.height);
  const canvas=rotateCanvas(original,angle);
  const sample=document.createElement('canvas');sample.width=64;sample.height=48;
  const sc=sample.getContext('2d',{willReadFrequently:true});if(!sc)return missing('canvas_unavailable');
  sc.drawImage(canvas,0,0,64,48);
  const rgba=sc.getImageData(0,0,64,48).data;
  const gray=Array.from({length:64*48},(_,i)=>(rgba[i*4]!+rgba[i*4+1]!+rgba[i*4+2]!)/3);
  const mean=gray.reduce((a,b)=>a+b,0)/gray.length;
  const contrast=Math.sqrt(gray.reduce((sum,x)=>sum+(x-mean)**2,0)/gray.length);
  const edge=gray.slice(1).reduce((sum,x,i)=>sum+Math.abs(x-gray[i]!),0)/gray.length;
  const difference=(a:number[],b:number[])=>a.reduce((sum,x,i)=>sum+Math.abs(x-b[i]!),0)/a.length;
  const movement=state.previous?difference(gray,state.previous):null;
  const duplicate=state.lastSent?difference(gray,state.lastSent)<3:false;
  state.previous=gray;
  const reason=automatic?(mean<30?'too_dark':mean>235?'too_bright':
    edge<2?'low_edges':movement!==null&&movement>20?'moving':duplicate?'duplicate':null):null;
  const base={mean,brightness:mean,contrast,edge,sharpness:sharpness(gray,64,48),
    movement,width:canvas.width,height:canvas.height,rotation};
  if(reason){
    observe?.({...base,capture_ms:performance.now()-started,accepted:false,reason});
    return null;
  }
  const image=canvas.toDataURL('image/jpeg',.9);
  const quality={...base,capture_ms:performance.now()-started};
  observe?.({...quality,accepted:true,reason:null});
  return {original,image,fingerprint:gray,quality};
}
