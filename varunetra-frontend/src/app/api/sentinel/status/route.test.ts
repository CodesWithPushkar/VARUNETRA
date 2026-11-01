import { afterEach, describe, expect, it, vi } from 'vitest';
import { GET } from './route';

afterEach(() => vi.unstubAllEnvs());

describe('Sentinel tile configuration status', () => {
  it('reports when no tile provider is configured', async () => {
    vi.stubEnv('SENTINEL_1_TILE_URL', '');
    expect(await (await GET()).json()).toEqual({ configured: false });
  });

  it('reports when a tile provider is configured', async () => {
    vi.stubEnv('SENTINEL_1_TILE_URL', 'https://tiles.example/{z}/{x}/{y}.png');
    expect(await (await GET()).json()).toEqual({ configured: true });
  });
});
