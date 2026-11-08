// Candidate selection is technical quality filtering, not composition detection.
export type FrameState={previous:number[]|null;lastSent:number[]|null;geometry?:string};
export function resetFrames(state:FrameState){state.previous=null;state.lastSent=null;state.geometry=undefined;}
export function quarterTurn(angle:number){return ((angle%360)+360)%360;}
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
export function capture(video:HTMLVideoElement,state:FrameState,automatic:boolean,angle=0) {
  if(video.readyState<2 || Math.max(video.videoWidth,video.videoHeight)<480 || Math.min(video.videoWidth,video.videoHeight)<320)return null;
  prepareFrameState(state,video.videoWidth,video.videoHeight,angle);
  const original=document.createElement('canvas');
  const scale=Math.min(1,1600/Math.max(video.videoWidth,video.videoHeight));
  original.width=Math.round(video.videoWidth*scale);original.height=Math.round(video.videoHeight*scale);
  const ctx=original.getContext('2d');if(!ctx)return null;
  ctx.drawImage(video,0,0,original.width,original.height);
  const canvas=rotateCanvas(original,angle);
  const sample=document.createElement('canvas');sample.width=64;sample.height=48;
  const sc=sample.getContext('2d',{willReadFrequently:true});if(!sc)return null;
  sc.drawImage(canvas,0,0,64,48);
  const rgba=sc.getImageData(0,0,64,48).data;
  const gray=Array.from({length:64*48},(_,i)=>(rgba[i*4]!+rgba[i*4+1]!+rgba[i*4+2]!)/3);
  const mean=gray.reduce((a,b)=>a+b,0)/gray.length;
  const edge=gray.slice(1).reduce((sum,x,i)=>sum+Math.abs(x-gray[i]!),0)/gray.length;
  const difference=(a:number[],b:number[])=>a.reduce((sum,x,i)=>sum+Math.abs(x-b[i]!),0)/a.length;
  const movement=state.previous?difference(gray,state.previous):Infinity;
  const duplicate=state.lastSent?difference(gray,state.lastSent)<3:false;
  state.previous=gray;
  if(automatic && (mean<30||mean>235||edge<2||movement>20||duplicate))return null;
  return {original,image:canvas.toDataURL('image/jpeg',.9),fingerprint:gray,quality:{mean,edge,movement:Number.isFinite(movement)?movement:null}};
}
