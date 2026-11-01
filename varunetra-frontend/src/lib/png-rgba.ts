/**
 * OSIRIS — a small PNG reader for value-encoded tiles.
 *
 * Mercator packs numbers into the four channels of a PNG, alpha included, so
 * the picture must come back byte for byte. A canvas can't give that: it
 * premultiplies colour by alpha, which ruins every pixel that isn't opaque.
 * This reads the file itself: 8-bit RGBA, not interlaced, which is what the
 * tile API serves.
 */

export interface RgbaImage {
  width: number;
  height: number;
  /** width × height × 4 bytes: R, G, B, A for each pixel, row by row. */
  data: Uint8Array;
}

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

/** zlib → bytes, with the browser's own decompressor. */
async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

export async function decodePngRgba(input: ArrayBuffer | Uint8Array): Promise<RgbaImage> {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (bytes.length < 8 || SIGNATURE.some((v, i) => bytes[i] !== v)) throw new Error('Not a PNG file');

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let width = 0;
  let height = 0;
  const idat: Uint8Array[] = [];
  let pos = 8;
  while (pos + 8 <= bytes.length) {
    const length = view.getUint32(pos);
    const type = String.fromCharCode(bytes[pos + 4], bytes[pos + 5], bytes[pos + 6], bytes[pos + 7]);
    const body = bytes.subarray(pos + 8, pos + 8 + length);
    if (type === 'IHDR') {
      width = view.getUint32(pos + 8);
      height = view.getUint32(pos + 12);
      const depth = bytes[pos + 16];
      const colour = bytes[pos + 17];
      const interlace = bytes[pos + 20];
      if (depth !== 8 || colour !== 6 || interlace !== 0) {
        throw new Error(`Unsupported PNG (bit depth ${depth}, colour type ${colour}, interlace ${interlace}); need 8-bit RGBA`);
      }
    } else if (type === 'IDAT') {
      idat.push(body);
    } else if (type === 'IEND') {
      break;
    }
    pos += 12 + length;
  }
  if (!width || !height || idat.length === 0) throw new Error('PNG has no image data');

  const joined = new Uint8Array(idat.reduce((n, part) => n + part.length, 0));
  let at = 0;
  for (const part of idat) {
    joined.set(part, at);
    at += part.length;
  }
  const raw = await inflate(joined);

  const bpp = 4;
  const stride = width * bpp;
  if (raw.length < (stride + 1) * height) throw new Error('PNG data is truncated');
  const data = new Uint8Array(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const src = y * (stride + 1) + 1;
    const row = y * stride;
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? data[row + i - bpp] : 0;
      const b = y > 0 ? data[row - stride + i] : 0;
      const c = y > 0 && i >= bpp ? data[row - stride + i - bpp] : 0;
      let value = raw[src + i];
      if (filter === 1) value += a;
      else if (filter === 2) value += b;
      else if (filter === 3) value += (a + b) >> 1;
      else if (filter === 4) value += paeth(a, b, c);
      else if (filter !== 0) throw new Error(`Unknown PNG filter ${filter}`);
      data[row + i] = value & 255;
    }
  }
  return { width, height, data };
}
