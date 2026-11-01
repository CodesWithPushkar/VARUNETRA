/**
 * VARUNETRA — abuse.ch Feodo Tracker C2 indicators.
 *
 * Feodo Tracker publishes a botnet C2 *blocklist*: one row per known
 * command-and-control server. A row says where a C2 is hosted and whether it
 * still answers. It does not say who is operating it, who it attacked, what
 * that attack did, or when any attack happened.
 *
 * The layer this module feeds used to claim all four. It looked up an
 * "attacker origin" in a hardcoded malware-family → region table, drew an arc
 * from there to the C2's country, picked a verb at random out of a list that
 * included EXFILTRATION and CREDENTIAL HARVEST, and cloned every row until it
 * had fifteen arcs so the animation looked busy. The clone count was then
 * reported as `total` under the provider's name, and the verb changed on every
 * cache refresh without any new evidence. None of that was in the source.
 *
 * So: no origins, no verbs, no clones. One indicator per C2 server, carrying
 * only fields the record actually contains. Placement is the centroid of the
 * hosting country — an approximation of a field that is in the record, flagged
 * as such via `location_precision`, not a guess at an unrecorded fact.
 */
import { centroidFor } from './countryCentroids';

/** One row of Feodo Tracker's `ipblocklist.json`, as published. */
export interface FeodoRecord {
  ip_address?: string;
  port?: number | string;
  /** Older dumps and some mirrors spell the port this way. */
  dst_port?: number | string;
  status?: string;
  hostname?: string | null;
  as_number?: number | null;
  as_name?: string | null;
  as_country?: string | null;
  /** Older dumps and some mirrors spell the hosting country this way. */
  country?: string | null;
  first_seen?: string | null;
  last_online?: string | null;
  malware?: string | null;
}

/** A C2 server as the map consumes it. Every field traces to the source row. */
export interface C2Indicator {
  /** Stable across refreshes: the C2's address and port. */
  id: string;
  ip: string;
  port: number | null;
  malware: string | null;
  /** 'online' or 'offline', as reported — an offline C2 is not an attack. */
  status: string;
  hostname: string | null;
  /** Country the C2 is *hosted* in. Not an attacker attribution. */
  country: string | null;
  as_number: number | null;
  as_name: string | null;
  /** When the provider first listed this C2, and when it last answered. */
  first_seen: string | null;
  last_online: string | null;
  /** Hosting-country centroid, or null when the country is unknown. */
  lng: number | null;
  lat: number | null;
  /** Always 'country' when coordinates are present — never a host fix. */
  location_precision: 'country' | null;
}

export const FEODO_SOURCE = 'abuse.ch Feodo Tracker';
export const FEODO_SOURCE_URL = 'https://feodotracker.abuse.ch/browse/';

function text(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function port(record: FeodoRecord): number | null {
  const raw = record.port ?? record.dst_port;
  const parsed = typeof raw === 'string' ? Number.parseInt(raw, 10) : raw;
  return typeof parsed === 'number' && Number.isFinite(parsed) ? parsed : null;
}

/** Builds one indicator, or null when the row carries no usable address. */
export function toIndicator(record: FeodoRecord): C2Indicator | null {
  const ip = text(record.ip_address);
  if (!ip) return null;

  const p = port(record);
  const country = text(record.as_country) ?? text(record.country);
  const centroid = centroidFor(country);

  return {
    id: p === null ? ip : `${ip}:${p}`,
    ip,
    port: p,
    malware: text(record.malware),
    status: text(record.status) ?? 'unknown',
    hostname: text(record.hostname),
    country: country ? country.toUpperCase() : null,
    as_number: typeof record.as_number === 'number' ? record.as_number : null,
    as_name: text(record.as_name),
    first_seen: text(record.first_seen),
    last_online: text(record.last_online),
    lng: centroid ? centroid[0] : null,
    lat: centroid ? centroid[1] : null,
    location_precision: centroid ? 'country' : null,
  };
}

/**
 * Parses a blocklist dump into deduplicated indicators.
 *
 * Deduplication is by `id` — the same C2 listed twice is one C2, not two
 * events. Order follows the source so identical input yields identical output.
 */
export function parseFeodoBlocklist(raw: unknown): C2Indicator[] {
  if (!Array.isArray(raw)) return [];

  const byId = new Map<string, C2Indicator>();
  for (const record of raw) {
    if (!record || typeof record !== 'object') continue;
    const indicator = toIndicator(record as FeodoRecord);
    if (indicator && !byId.has(indicator.id)) byId.set(indicator.id, indicator);
  }
  return [...byId.values()];
}
