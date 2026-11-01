import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';

const request = (mmsi: string) =>
  GET(new NextRequest('http://localhost'), { params: Promise.resolve({ mmsi }) });

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('Global Fishing Watch vessel lookup', () => {
  it('rejects invalid MMSIs without making an upstream request', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);

    const response = await request('12345');

    expect(response.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('reports when the private server token is not configured', async () => {
    vi.stubEnv('GFW_API_TOKEN', '');

    const response = await request('412331038');

    expect(response.status).toBe(503);
  });

  it('requests all matching identity and ownership details with the private token', async () => {
    vi.stubEnv('GFW_API_TOKEN', 'server-only-token');
    const payload = { total: 2, entries: [{ id: 'one' }, { id: 'two' }] };
    const fetch = vi.fn<
      (input: RequestInfo | URL, options?: RequestInit) => Promise<Response>
    >(() => Promise.resolve(Response.json(payload)));
    vi.stubGlobal('fetch', fetch);

    const response = await request('412331038');
    const [requestUrl, options] = fetch.mock.calls[0]!;
    const url = new URL(String(requestUrl));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(payload);
    expect(requestUrl).toBeInstanceOf(URL);
    expect(url.searchParams.get('query')).toBe('412331038');
    expect(url.searchParams.get('datasets[0]')).toBe('public-global-vessel-identity:latest');
    expect(url.searchParams.get('includes[0]')).toBe('MATCH_CRITERIA');
    expect(url.searchParams.get('includes[1]')).toBe('OWNERSHIP');
    expect(options?.headers).toEqual({
      Accept: 'application/json',
      Authorization: 'Bearer server-only-token',
    });
  });

  it('returns the upstream rejection reason without exposing the token', async () => {
    vi.stubEnv('GFW_API_TOKEN', 'server-only-token');
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      error: 'Token lacks access to requested dataset',
    }, { status: 403 })));

    const response = await request('412331038');
    const payload = await response.json();

    expect(response.status).toBe(502);
    expect(payload.error).toContain('HTTP 403');
    expect(payload.error).toContain('Token lacks access to requested dataset');
    expect(payload.error).not.toContain('server-only-token');
  });
});
