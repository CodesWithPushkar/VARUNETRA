import { NextResponse, type NextRequest } from 'next/server';

const configuredApiUrl = process.env.VARUNETRA_API_URL?.trim() ||
  process.env.NEXT_PUBLIC_VARUNETRA_API_URL?.replace(/\/api\/v1\/(?:slicks|spills)\/?$/, '');
const VARUNETRA_API_URL = configuredApiUrl ??
  (process.env.NODE_ENV === 'production' ? 'http://varunetra-backend:3001' : 'http://127.0.0.1:3001');

export async function proxyVessels(request: NextRequest, path: string) {
  const upstreamUrl = new URL(path, VARUNETRA_API_URL);
  upstreamUrl.search = request.nextUrl.search;

  try {
    const upstream = await fetch(upstreamUrl, {
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: {
        'Content-Type': upstream.headers.get('content-type') ?? 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  } catch (error) {
    console.error('[VARUNETRA] AIS vessel proxy failed:', error);
    return NextResponse.json({ error: 'AIS vessel service unavailable' }, { status: 502 });
  }
}