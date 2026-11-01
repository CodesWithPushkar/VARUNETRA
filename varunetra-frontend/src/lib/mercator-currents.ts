import { decodePngRgba } from './png-rgba';
import type { OceanCurrentGrid } from './currents-gl-layer';

const API_ROOT = 'https://api.mercator.blue';
const CATALOG_URL = `${API_ROOT}/catalog.json`;
const DATASET = 'currents';

const ZOOM = 2;
const STEP = 0.25;
const TOP_LAT = 85;
const BOTTOM_LAT = -85;

export const CURRENT_DRAW_GAIN = 20;

export interface MercatorCurrents {
  grid: OceanCurrentGrid;
  cycle: string;
  hour: string;
}

interface Link { rel: string; href: string; }

interface Item {
  properties: {
    'mercator:encoding': { scale: number; offset: number };
    'mercator:tile': { maxzoom: number };
  };
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Mercator ${res.status} for ${url}`);
  return res.json() as Promise<T>;
}

function newestItem(hrefs: string[]): string {
  return [...hrefs].sort((a, b) => {
    const [ca, fa] = a.split('/');
    const [cb, fb] = b.split('/');
    if (ca !== cb) return ca < cb ? 1 : -1;
    return fa < fb ? -1 : fa > fb ? 1 : 0;
  })[0];
}

export interface Mosaic {
  size: number;
  u: Float32Array;
  v: Float32Array;
  valid: Uint8Array;
}

export function paintTile(mosaic: Mosaic, rgba: Uint8Array, tileSize: number, tx: number, ty: number, scale: number, offset: number): void {
  for (let py = 0; py < tileSize; py++) {
    for (let px = 0; px < tileSize; px++) {
      const o = (py * tileSize + px) * 4;
      const r = rgba[o];
      const g = rgba[o + 1];
      const b = rgba[o + 2];
      const a = rgba[o + 3];
      if (!(r | g | b | a)) continue;
      const at = (ty * tileSize + py) * mosaic.size + tx * tileSize + px;
      mosaic.u[at] = (r * 256 + g) * scale + offset;
      mosaic.v[at] = (b * 256 + a) * scale + offset;
      mosaic.valid[at] = 1;
    }
  }
}

export function resampleToGrid(mosaic: Mosaic, gain = CURRENT_DRAW_GAIN): OceanCurrentGrid {
  const { size, u, v, valid } = mosaic;
  const ni = Math.round(360 / STEP);
  const nj = Math.round((TOP_LAT - BOTTOM_LAT) / STEP) + 1;
  const gu = new Float32Array(ni * nj);
  const gv = new Float32Array(ni * nj);
  const wrap = (x: number) => ((x % size) + size) % size;

  for (let j = 0; j < nj; j++) {
    const lat = TOP_LAT - j * STEP;
    const y = (0.5 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / (2 * Math.PI)) * size;
    const fy = y - 0.5;
    const y0 = Math.floor(fy);
    const wy = fy - y0;
    const ny = Math.min(size - 1, Math.max(0, Math.floor(y)));
    for (let i = 0; i < ni; i++) {
      const x = ((-180 + i * STEP + 180) / 360) * size;
      if (!valid[ny * size + wrap(Math.floor(x))]) continue;
      const fx = x - 0.5;
      const x0 = Math.floor(fx);
      const wx = fx - x0;
      let su = 0;
      let sv = 0;
      let sw = 0;
      for (let dy = 0; dy < 2; dy++) {
        const yy = Math.min(size - 1, Math.max(0, y0 + dy));
        for (let dx = 0; dx < 2; dx++) {
          const at = yy * size + wrap(x0 + dx);
          if (!valid[at]) continue;
          const w = (dx ? wx : 1 - wx) * (dy ? wy : 1 - wy);
          su += u[at] * w;
          sv += v[at] * w;
          sw += w;
        }
      }
      if (sw > 0) {
        gu[j * ni + i] = (su / sw) * gain;
        gv[j * ni + i] = (sv / sw) * gain;
      }
    }
  }
  return { ni, nj, la1: TOP_LAT, lo1: -180, dx: STEP, dy: STEP, u: gu, v: gv };
}

async function load(apiKey: string): Promise<MercatorCurrents> {
  const catalog = await getJson<{ links: Link[] }>(CATALOG_URL);
  const child = catalog.links.find(l => l.rel === 'child' && (l.href === `tiles/${DATASET}/collection.json` || l.href.endsWith(`/${DATASET}/collection.json`)));
  if (!child) throw new Error(`Mercator has no "${DATASET}" dataset for this key`);
  const collectionUrl = new URL(child.href, CATALOG_URL).toString();
  const collection = await getJson<{ links: Link[] }>(collectionUrl);
  const hrefs = collection.links.filter(l => l.rel === 'item').map(l => l.href);
  if (hrefs.length === 0) throw new Error('Mercator has no ocean-current data published');
  const itemHref = newestItem(hrefs);
  const itemUrl = new URL(itemHref, collectionUrl).toString();
  const item = await getJson<Item>(itemUrl);
  const { scale, offset } = item.properties['mercator:encoding'];
  const zoom = Math.min(ZOOM, item.properties['mercator:tile'].maxzoom);
  const base = itemUrl.replace(/\/item\.json$/, '');
  const [cycle, hour] = itemHref.split('/');

  const n = 2 ** zoom;
  const jobs: Promise<{ tx: number; ty: number; image: Awaited<ReturnType<typeof decodePngRgba>> }>[] = [];
  for (let ty = 0; ty < n; ty++) {
    for (let tx = 0; tx < n; tx++) {
      const url = `${base}/${zoom}/${tx}/${ty}.png?apiKey=${encodeURIComponent(apiKey)}`;
      jobs.push((async () => {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Mercator tile ${zoom}/${tx}/${ty}: HTTP ${res.status}`);
        return { tx, ty, image: await decodePngRgba(await res.arrayBuffer()) };
      })());
    }
  }
  const settled = await Promise.allSettled(jobs);
  const tiles = settled.flatMap(s => (s.status === 'fulfilled' ? [s.value] : []));
  if (tiles.length === 0) {
    const first = settled.find((s): s is PromiseRejectedResult => s.status === 'rejected');
    throw first?.reason ?? new Error('No ocean-current tiles could be loaded');
  }
  if (tiles.length < jobs.length) console.warn(`[VARUNETRA] Mercator currents: ${jobs.length - tiles.length} of ${jobs.length} tiles failed; those areas are blank.`);

  const tileSize = tiles[0].image.width;
  const size = tileSize * n;
  const mosaic: Mosaic = { size, u: new Float32Array(size * size), v: new Float32Array(size * size), valid: new Uint8Array(size * size) };
  for (const { tx, ty, image } of tiles) {
    if (image.width !== tileSize || image.height !== tileSize) continue;
    paintTile(mosaic, image.data, tileSize, tx, ty, scale, offset);
  }
  return { grid: resampleToGrid(mosaic), cycle, hour };
}

let cached: { key: string; load: Promise<MercatorCurrents> } | null = null;

export function loadMercatorCurrents(apiKey: string): Promise<MercatorCurrents> {
  if (!cached || cached.key !== apiKey) {
    const promise = load(apiKey);
    cached = { key: apiKey, load: promise };
    promise.catch(() => {
      if (cached?.load === promise) cached = null;
    });
  }
  return cached.load;
}
