import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

type TileParams = { z: string; x: string; y: string };

export async function GET(_request: Request, { params }: { params: Promise<TileParams> }) {
  const { z: zText, x: xText, y: yText } = await params;
  const z = Number(zText);
  const x = Number(xText);
  const y = Number(yText);

  if (![z, x, y].every(Number.isInteger) || z < 0 || z > 22 || x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z) {
    return NextResponse.json({ error: 'Invalid Sentinel-1 tile coordinates' }, { status: 400 });
  }

  const template = process.env.SENTINEL_1_TILE_URL;
  if (!template) {
    return NextResponse.json({ error: 'Sentinel-1 tile provider is not configured' }, { status: 503 });
  }

  try {
    const target = new URL(template
      .replaceAll('{z}', String(z))
      .replaceAll('{x}', String(x))
      .replaceAll('{y}', String(y)));
    if (target.protocol !== 'https:') {
      return NextResponse.json({ error: 'Sentinel-1 tile provider must use HTTPS' }, { status: 500 });
    }

    const response = await fetch(target, { signal: AbortSignal.timeout(20_000), cache: 'force-cache' });
    if (!response.ok) {
      return NextResponse.json({ error: 'Sentinel-1 tile provider request failed' }, { status: 502 });
    }

    const contentType = response.headers.get('content-type') ?? 'application/octet-stream';
    if (!contentType.startsWith('image/')) {
      return NextResponse.json({ error: 'Sentinel-1 tile provider returned a non-image response' }, { status: 502 });
    }

    return new NextResponse(await response.arrayBuffer(), {
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
      },
    });
  } catch (error) {
    console.warn('[VARUNETRA] Sentinel-1 tile proxy:', error);
    return NextResponse.json({ error: 'Sentinel-1 tile request failed' }, { status: 502 });
  }
}
