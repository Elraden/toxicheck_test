
import test from 'node:test';
import assert from 'node:assert/strict';
import {rotatedSize,quarterTurn,previewRotation,prepareFrameState,resetFrames} from '../src/research/orchestration/capture.ts';

test('quarter turns preserve all frame dimensions for portrait, landscape and odd sizes',()=>{
  for(const [width,height] of [[1920,1080],[720,1280],[641,479],[320,480]]){
    for(const angle of [0,90,180,270,360]){
      const size=rotatedSize(width,height,angle);
      assert.equal(size.width*size.height,width*height);
      assert.deepEqual(size,angle%180?{width:height,height:width}:{width,height});
    }
  }
  assert.equal(quarterTurn(360),0);
  assert.throws(()=>rotatedSize(1,2,45));
});
test('rotation and intrinsic resolution changes invalidate both quality fingerprints',()=>{
  const state={previous:null,lastSent:null};
  prepareFrameState(state,1920,1080,0);
  state.previous=[1];state.lastSent=[2];
  prepareFrameState(state,1920,1080,360);
  assert.deepEqual(state.previous,[1]);assert.deepEqual(state.lastSent,[2]);
  for(const [w,h,angle] of [[1920,1080,90],[1080,1920,90],[1080,1920,180]]){
    state.previous=[1];state.lastSent=[2];prepareFrameState(state,w,h,angle);
    assert.equal(state.previous,null);assert.equal(state.lastSent,null);
  }
  state.previous=[1];state.lastSent=[2];resetFrames(state);
  assert.equal(state.geometry,undefined);assert.equal(state.previous,null);assert.equal(state.lastSent,null);
});

test('live preview and OCR share the same orientation for portrait and landscape input',()=>{
  assert.equal(previewRotation(1080,1920,'portrait',0),0);
  assert.equal(previewRotation(1080,1920,'landscape',0),90);
  assert.equal(previewRotation(1920,1080,'landscape',0),0);
  assert.equal(previewRotation(1920,1080,'portrait',0),90);
  assert.equal(previewRotation(1080,1920,'landscape',90),180);
  assert.equal(previewRotation(1920,1080,'portrait',270),0);
  const a=rotatedSize(1080,1920,previewRotation(1080,1920,'landscape',0));
  assert.deepEqual(a,{width:1920,height:1080});
});
