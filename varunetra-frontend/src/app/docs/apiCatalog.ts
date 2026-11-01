/** Machine-readable descriptions of the marine map's public API routes. */

export type HttpMethod = 'GET' | 'POST' | 'DELETE';

export interface ApiParam {
  name: string;
  required?: boolean;
  desc: string;
  example?: string;
}

export interface ApiEndpoint {
  path: string;
  method: HttpMethod | HttpMethod[];
  summary: string;
  params?: ApiParam[];
  returns: string[];
  notes?: string;
  env?: string[];
  bodyExample?: string;
  requiresAuth?: boolean;
  headers?: Record<string, string>;
}

export function endpointId(ep: ApiEndpoint): string {
  const method = Array.isArray(ep.method) ? ep.method[0] : ep.method;
  return `ep-${method}-${ep.path.replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '')}`.toLowerCase();
}

export function sampleUrl(ep: ApiEndpoint, origin = ''): string {
  const qs = (ep.params || [])
    .filter(p => p.required || p.example)
    .map(p => `${p.name}=${encodeURIComponent((p.example || '').split(' | ')[0] || 'value')}`)
    .join('&');
  return `${origin}${ep.path}${qs ? `?${qs}` : ''}`;
}

export interface ApiGroup {
  id: string;
  title: string;
  blurb: string;
  endpoints: ApiEndpoint[];
}

export const API_GROUPS: ApiGroup[] = [
  {
    id: 'marine-map',
    title: 'Marine Map',
    blurb: 'Map tiles, global wind fields, and configurable Sentinel-1 SAR imagery.',
    endpoints: [
      {
        path: '/api/proxy-tiles',
        method: 'GET',
        summary: 'Same-origin proxy for Carto Dark Matter style, fonts, sprites, and tiles.',
        params: [{ name: 'url', required: true, desc: 'HTTPS URL on `*.basemaps.cartocdn.com`.' }],
        returns: ['…binary map resource'],
      },
      {
        path: '/api/wind',
        method: 'GET',
        summary: 'Lists available global NOAA GFS wind times or returns a selected u/v wind field.',
        params: [
          { name: 'run', desc: 'Forecast run timestamp.', example: '2026-10-28T00:00:00.000Z' },
          { name: 't', desc: 'Requested frame timestamp.', example: '2026-10-28T00:00:00.000Z' },
        ],
        returns: ['run', 'times', 'ni', 'nj', 'u', 'v'],
        notes: 'The wind streamline overlay requests the latest run and its selected u/v field frames.',
      },
      {
        path: '/api/sentinel/status',
        method: 'GET',
        summary: 'Reports whether the server has a Sentinel-1 tile provider configured.',
        returns: ['configured'],
        notes: 'The map checks this before adding the SAR raster layer, avoiding requests to an unavailable tile proxy.',
      },
      {
        path: '/api/sentinel/tiles/{z}/{x}/{y}',
        method: 'GET',
        summary: 'Proxies a Sentinel-1 SAR map tile from the configured upstream tile provider.',
        returns: ['image/*'],
        env: ['SENTINEL_1_TILE_URL'],
        notes: 'Set `SENTINEL_1_TILE_URL` to an HTTPS XYZ tile URL template containing `{z}`, `{x}`, and `{y}`. Provider credentials remain server-side.',
      },
    ],
  },
];

export const ENDPOINT_COUNT = API_GROUPS.reduce((n, group) => n + group.endpoints.length, 0);
