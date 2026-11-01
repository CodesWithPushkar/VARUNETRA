import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const GFW_VESSEL_SEARCH_URL = 'https://gateway.api.globalfishingwatch.org/v3/vessels/search';

async function upstreamErrorDetail(response: Response, token: string): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (typeof payload !== 'object' || payload === null) return '';
    const record = payload as Record<string, unknown>;
    const detail = typeof record.message === 'string'
      ? record.message
      : typeof record.error === 'string'
        ? record.error
        : '';
    return detail.replaceAll(token, '[redacted]').slice(0, 300);
  } catch {
    return '';
  }
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ mmsi: string }> },
) {
  const { mmsi } = await params;
  if (!/^\d{9}$/.test(mmsi)) {
    return NextResponse.json({ error: 'MMSI must be a nine-digit number.' }, { status: 400 });
  }

  const token = process.env.GFW_API_TOKEN;
  if (!token) {
    return NextResponse.json({ error: 'Global Fishing Watch is not configured on the server.' }, { status: 503 });
  }

  const url = new URL(GFW_VESSEL_SEARCH_URL);
  url.searchParams.set('query', mmsi);
  url.searchParams.set('datasets[0]', 'public-global-vessel-identity:latest');
  url.searchParams.set('includes[0]', 'MATCH_CRITERIA');
  url.searchParams.set('includes[1]', 'OWNERSHIP');

  try {
    const response = await fetch(url, {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${token}`,
      },
      cache: 'no-store',
      signal: AbortSignal.timeout(15_000),
    });

    if (!response.ok) {
      const detail = await upstreamErrorDetail(response, token);
      const category = response.status === 401 || response.status === 403
        ? 'Global Fishing Watch rejected the token or request permissions'
        : 'Global Fishing Watch vessel lookup failed';
      const message = `${category} (HTTP ${response.status})${detail ? `: ${detail}` : '.'}`;
      return NextResponse.json({ error: message }, { status: 502 });
    }

    const payload: unknown = await response.json();
    if (
      typeof payload !== 'object' ||
      payload === null ||
      !('entries' in payload) ||
      !Array.isArray(payload.entries)
    ) {
      console.error('[VARUNETRA] Global Fishing Watch returned an invalid vessel search response.');
      return NextResponse.json({ error: 'Global Fishing Watch returned an invalid response.' }, { status: 502 });
    }

    return NextResponse.json(payload, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    console.error('[VARUNETRA] Global Fishing Watch vessel lookup failed:', error);
    return NextResponse.json({ error: 'Unable to reach Global Fishing Watch.' }, { status: 502 });
  }
}
