import assert from 'node:assert/strict';
import test from 'node:test';
import { rotatedDimensions } from '../src/services/imageOrientation.ts';

test('quarter turns swap dimensions without losing portrait/landscape pixels', () => {
  for (const turns of [-3, -1, 1, 3, 5]) {
    assert.deepEqual(rotatedDimensions(1920, 1080, turns), { width: 1080, height: 1920 });
  }
  for (const turns of [-4, -2, 0, 2, 4]) {
    assert.deepEqual(rotatedDimensions(1920, 1080, turns), { width: 1920, height: 1080 });
  }
});
