import { describe, expect, it } from 'vitest';
import { CURRENT_DRAW_GAIN, paintTile, resampleToGrid, type Mosaic } from './mercator-currents';

describe('Mercator current helpers', () => {
  it('packs value-encoded pixels into a mosaic', () => {
    const size = 2;
    const mosaic: Mosaic = {
      size,
      u: new Float32Array(size * size),
      v: new Float32Array(size * size),
      valid: new Uint8Array(size * size),
    };
    const rgba = new Uint8Array([
      1, 2, 3, 4,
      5, 6, 7, 8,
      9, 10, 11, 12,
      13, 14, 15, 16,
    ]);
    paintTile(mosaic, rgba, 2, 0, 0, 0.1, -1);

    expect(mosaic.u[0]).toBeCloseTo((1 * 256 + 2) * 0.1 - 1);
    expect(mosaic.v[0]).toBeCloseTo((3 * 256 + 4) * 0.1 - 1);
    expect(mosaic.valid[0]).toBe(1);
    expect(mosaic.valid[3]).toBe(1);
  });

  it('resamples a valid mosaic onto the lat/lon grid with draw gain applied', () => {
    const size = 4;
    const mosaic: Mosaic = {
      size,
      u: new Float32Array(size * size),
      v: new Float32Array(size * size),
      valid: new Uint8Array(size * size),
    };
    for (let i = 0; i < size; i++) {
      for (let j = 0; j < size; j++) {
        const at = j * size + i;
        mosaic.u[at] = 1;
        mosaic.v[at] = 2;
        mosaic.valid[at] = 1;
      }
    }
    const grid = resampleToGrid(mosaic, CURRENT_DRAW_GAIN);
    expect(grid.ni).toBe(1440);
    expect(grid.nj).toBe(681);
    expect(grid.dx).toBe(0.25);
    expect(grid.dy).toBe(0.25);
    expect(grid.u.length).toBe(grid.ni * grid.nj);
    expect(grid.v.length).toBe(grid.ni * grid.nj);
    expect(grid.u[0]).toBeGreaterThanOrEqual(0);
  });
});
