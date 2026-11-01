import { afterEach, describe, expect, it, vi } from 'vitest';
import { GET } from './route';

const request = (z: string, x: string, y: string) =>
  GET(new Request('http://localhost'), { params: Promise.resolve({ z, x, y }) });

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('Sentinel-1 tile proxy', () => {
  it('rejects invalid XYZ coordinates', async () => {
    const response = await request('2', '4', '0');
    expect(response.status).toBe(400);
  });

  it('reports an unconfigured provider explicitly', async () => {
    vi.stubEnv('SENTINEL_1_TILE_URL', '');
    const response = await request('2', '1', '3');
    expect(response.status).toBe(503);
  });

  it('proxies an image tile with the requested coordinates', async () => {
    vi.stubEnv('SENTINEL_1_TILE_URL', 'https://tiles.example/{z}/{x}/{y}.png');
    const fetch = vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), {
      headers: { 'content-type': 'image/png' },
    }));
    vi.stubGlobal('fetch', fetch);

    const response = await request('3', '2', '5');

    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('image/png');
    expect(fetch).toHaveBeenCalledWith(
      new URL('https://tiles.example/3/2/5.png'),
      expect.objectContaining({ cache: 'force-cache' }),
    );
  });
});
