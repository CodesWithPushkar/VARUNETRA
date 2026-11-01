import type { NextRequest } from 'next/server';
import { proxyVessels } from '@/lib/intel-vessels-proxy';

export const dynamic = 'force-dynamic';

export function GET(request: NextRequest, context: { params: Promise<{ mmsi: string }> }) {
  return context.params.then(({ mmsi }) =>
    proxyVessels(request, `/api/v1/vessels/${encodeURIComponent(mmsi)}`)
  );
}