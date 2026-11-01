import type { NextRequest } from 'next/server';
import { proxyVessels } from '@/lib/intel-vessels-proxy';

export const dynamic = 'force-dynamic';

export function GET(request: NextRequest) {
  return proxyVessels(request, '/api/v1/vessels');
}