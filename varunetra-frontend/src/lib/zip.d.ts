import type { Buffer } from 'node:buffer';

export interface ZipEntry {
  name: string;
  compressedSize: number;
  uncompressedSize: number;
  localHeaderOffset: number;
  compressionMethod: number;
}

export function inflateEntry(entry: ZipEntry, data: Buffer): Buffer;
export function localDataStart(data: Buffer): number;
export function locateCentralDirectory(tail: Buffer, tailOffset: number): { offset: number; size: number };
export function parseCentralDirectory(data: Buffer): ZipEntry[];
export function readEntry(archive: Buffer, predicate: (name: string) => boolean): Buffer | null;
export function unzipFirstEntry(archive: Buffer): Buffer;
