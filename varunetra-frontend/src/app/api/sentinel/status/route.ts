import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

export function GET() {
  return NextResponse.json(
    { configured: Boolean(process.env.SENTINEL_1_TILE_URL) },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
