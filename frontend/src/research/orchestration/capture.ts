// Candidate selection is technical quality filtering, not composition detection.
export type FrameState={previous:number[]|null;lastSent:number[]|null};
export function capture(video:HTMLVideoElement,state:FrameState,automatic:boolean) {
  if(video.readyState<2 || video.videoWidth<480 || video.videoHeight<320)return null;
  const canvas=document.createElement('canvas');
  const scale=Math.min(1,1600/Math.max(video.videoWidth,video.videoHeight));
  canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
  const ctx=canvas.getContext('2d');if(!ctx)return null;
  ctx.drawImage(video,0,0,canvas.width,canvas.height);
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
  return {image:canvas.toDataURL('image/jpeg',.9),fingerprint:gray,quality:{mean,edge,movement:Number.isFinite(movement)?movement:null}};
}
