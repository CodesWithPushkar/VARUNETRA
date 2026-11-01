import { useEffect, useRef, useState } from 'react';
import { ChevronRight, Ship as ShipIcon } from 'lucide-react';
import type { ReportedCulprit } from '@/types/slick';

interface ShipsTabProps {
  culprits: ReportedCulprit[];
  detectionDate: string | null;
  isLoading: boolean;
  error: string | null;
  onVesselHover: (id: string | null) => void;
  onVesselSelect: (id: string) => void;
}

function probabilityColor(probability: number): string {
  if (probability < 25) return 'bg-amber-400';
  if (probability <= 60) return 'bg-orange-500';
  return 'bg-red-500';
}

function mockIndicators(culprit: ReportedCulprit): { probability: number; distanceKm: number } {
  const seed = String(culprit.mmsi ?? culprit.id);
  const hash = [...seed].reduce((value, character) => (value * 31 + character.charCodeAt(0)) >>> 0, 7);
  return {
    probability: 30 + (hash % 50),
    distanceKm: 1 + ((hash >>> 5) % 400) / 10,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function findField(value: unknown, aliases: string[]): unknown {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findField(item, aliases);
      if (found !== undefined && found !== null && found !== '') return found;
    }
    return undefined;
  }
  if (!isRecord(value)) return undefined;

  for (const [key, fieldValue] of Object.entries(value)) {
    if (aliases.includes(normalizedKey(key)) && fieldValue !== undefined && fieldValue !== null && fieldValue !== '') {
      return fieldValue;
    }
  }
  for (const fieldValue of Object.values(value)) {
    const found = findField(fieldValue, aliases);
    if (found !== undefined && found !== null && found !== '') return found;
  }
  return undefined;
}

function mmsiFrom(value: unknown): string | null {
  const found = findField(value, ['mmsi', 'ssvid']);
  if (typeof found !== 'string' && typeof found !== 'number') return null;
  const digits = String(found).replace(/\D/g, '');
  return /^\d{9}$/.test(digits) ? digits : null;
}

function dateFrom(value: unknown): string | null {
  const found = findField(value, [
    'datedetected',
    'detectedat',
    'reportdate',
    'firsttransmissiondate',
    'lasttransmissiondate',
    'validfrom',
    'validto',
    'timestamp',
    'date',
  ]);
  if (typeof found !== 'string' && typeof found !== 'number') return null;
  const parsed = new Date(found);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function detectionDateDistance(recordDate: string | null, detectionDate: string | null): number {
  if (!recordDate || !detectionDate) return Number.POSITIVE_INFINITY;
  const recordTime = Date.parse(recordDate);
  const detectionTime = Date.parse(detectionDate);
  return Number.isFinite(recordTime) && Number.isFinite(detectionTime)
    ? Math.abs(recordTime - detectionTime)
    : Number.POSITIVE_INFINITY;
}

function displayValue(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    const items = value.map(displayValue).filter((item): item is string => item !== null);
    return items.length > 0 ? items.join(', ') : null;
  }
  if (isRecord(value)) {
    for (const key of ['value', 'name', 'label']) {
      if (key in value) {
        const unwrapped = displayValue(value[key]);
        if (unwrapped) return unwrapped;
      }
    }
  }
  return null;
}

function vesselDetailFields(details: unknown, mmsi: string, detectedAt: string | null): Array<[string, string]> {
  const priority: Array<[string, string[]]> = [
    ['Ship name', ['shipname', 'name']],
    ['MMSI', ['mmsi', 'ssvid']],
    ['IMO', ['imo', 'imonumber']],
    ['Call sign', ['callsign']],
    ['Flag', ['flag', 'flagcountry']],
    ['Vessel type', ['shiptype', 'vesseltype', 'type']],
    ['Gear type', ['geartype']],
    ['Length', ['length', 'lengthoverall']],
    ['Gross tonnage', ['grosstonnage', 'tonnage', 'gt']],
    ['Owner', ['owner', 'registeredowner']],
    ['Operator', ['operator', 'manager']],
  ];
  const fields: Array<[string, string]> = [];
  const seen = new Set<string>();

  for (const [label, aliases] of priority) {
    const raw = findField(details, aliases);
    const value = label === 'MMSI' ? mmsi : displayValue(raw);
    if (!value) continue;
    fields.push([label, value]);
    aliases.forEach(alias => seen.add(alias));
  }
  if (!fields.some(([label]) => label === 'Ship name')) {
    fields.unshift(['Ship name', 'Ship']);
  }
  if (detectedAt) fields.push(['Date detected', new Date(detectedAt).toLocaleString()]);

  const collectAdditional = (value: unknown, depth = 0): void => {
    if (depth > 5 || value === null || value === undefined) return;
    if (Array.isArray(value)) {
      value.forEach(item => collectAdditional(item, depth + 1));
      return;
    }
    if (!isRecord(value)) return;
    for (const [key, nestedValue] of Object.entries(value)) {
      const normalized = normalizedKey(key);
      if (normalized === 'selfreportedinfo' || normalized === 'dataset' || seen.has(normalized)) continue;
      const label = key
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .replace(/[_-]+/g, ' ')
        .replace(/\b\w/g, character => character.toUpperCase());
      const rendered = displayValue(nestedValue);
      if (rendered && !fields.some(([existingLabel]) => existingLabel === label)) {
        fields.push([label, rendered]);
      } else if (!rendered) {
        collectAdditional(nestedValue, depth + 1);
      }
    }
  };
  collectAdditional(details);
  return fields.slice(0, 15);
}

function VesselIdentityDetails({ mmsi, detectionDate }: { mmsi: string; detectionDate: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const [entries, setEntries] = useState<unknown[] | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  const loadDetails = async () => {
    const controller = new AbortController();
    requestRef.current = controller;
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/gfw/vessels/${encodeURIComponent(mmsi)}`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        const payload: unknown = await response.json();
        const message = isRecord(payload) && typeof payload.error === 'string'
          ? payload.error
          : `Vessel details request failed (${response.status}).`;
        throw new Error(message);
      }

      const payload: unknown = await response.json();
      if (!isRecord(payload) || !Array.isArray(payload.entries)) {
        throw new Error('Global Fishing Watch returned an invalid vessel search response.');
      }
      if (!controller.signal.aborted) setEntries(payload.entries);
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : 'Unable to load vessel details.');
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        if (!controller.signal.aborted) setIsLoading(false);
      }
    }
  };

  const toggleDetails = () => {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (entries === null && !isLoading) void loadDetails();
  };

  return (
    <>
      <button
        type="button"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-white/10 text-slate-400 transition-colors hover:bg-white/5 hover:text-orange-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"
        aria-label={`${expanded ? 'Hide' : 'Show'} Global Fishing Watch details for MMSI ${mmsi}`}
        aria-expanded={expanded}
        onClick={toggleDetails}
      >
        <ChevronRight size={18} className={`transition-transform ${expanded ? 'rotate-90' : ''}`} />
      </button>
      {expanded && (
        <div className="basis-full min-w-0 border-t border-white/10 pt-3">
          {isLoading ? (
            <p role="status" className="text-xs text-slate-400">Fetching vessel details…</p>
          ) : error ? (
            <p role="alert" className="text-xs leading-5 text-amber-200">{error}</p>
          ) : entries ? (() => {
            const matches = entries
              .filter(isRecord)
              .map((entry, index) => {
                const selfReportedInfo = findField(entry, ['selfreportedinfo']);
                const details = selfReportedInfo ?? entry;
                const foundMmsi = mmsiFrom(details) ?? mmsiFrom(entry);
                return {
                  details,
                  index,
                  foundMmsi,
                  detectedAt: dateFrom(details) ?? dateFrom(entry),
                  name: findField(details, ['name']),
                  shipname: findField(details, ['shipname']),
                  dataset: entry.dataset,
                };
              })
              .filter(match => match.foundMmsi === mmsi)
              .sort((left, right) =>
                detectionDateDistance(left.detectedAt, detectionDate) -
                detectionDateDistance(right.detectedAt, detectionDate)
              );
            return matches.length === 0 ? (
              <p className="text-xs text-slate-400">No Global Fishing Watch record matched MMSI {mmsi}.</p>
            ) : (
            <div className="space-y-2">
              <p className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                Matching vessel records ({matches.length})
              </p>
              {matches.map(({ details, detectedAt, name, shipname, dataset, index }, matchIndex) => {
                const vesselName = typeof name === 'string' && name.trim()
                  ? name
                  : typeof shipname === 'string' && shipname.trim()
                    ? shipname
                    : 'Ship';
                const fields = vesselDetailFields(details, mmsi, detectedAt);
                return (
                  <article key={`${String(dataset ?? 'match')}-${index}`} className="rounded border border-white/10 bg-black/20 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-medium text-slate-100">{vesselName}</h3>
                        <p className="mt-1 text-[10px] text-slate-500">
                          {matchIndex === 0 ? 'Closest record to detection date' : 'Matching vessel record'}
                        </p>
                      </div>
                      {detectedAt && (
                        <time dateTime={detectedAt} className="shrink-0 text-right text-[10px] text-slate-400">
                          Detected<br />{new Date(detectedAt).toLocaleDateString()}
                        </time>
                      )}
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-white/10 pt-3">
                      {fields.map(([label, value]) => (
                        <div key={label} className="min-w-0">
                          <dt className="text-[9px] uppercase tracking-wider text-slate-500">{label}</dt>
                          <dd className="mt-0.5 break-words text-xs text-slate-200">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </article>
                );
              })}
            </div>
            );
          })() : null}
        </div>
      )}
    </>
  );
}

export default function ShipsTab({
  culprits,
  detectionDate,
  isLoading,
  error,
  onVesselHover,
  onVesselSelect,
}: ShipsTabProps) {
  return (
    <div className="space-y-2">
      <p className="pb-2 font-mono text-[11px] uppercase tracking-widest text-slate-500">
        Reported vessels
      </p>
      <p className="pb-2 text-[10px] leading-4 text-slate-500">
        Distance and attribution score are illustrative placeholders, not database values.
      </p>
      {isLoading ? (
        <p role="status" className="rounded-md border border-white/10 p-4 text-sm text-slate-400">
          Loading reported vessels…
        </p>
      ) : error ? (
        <p role="alert" className="rounded-md border border-white/10 p-4 text-sm text-red-300">
          {error}
        </p>
      ) : culprits.length === 0 ? (
        <div className="rounded-md border border-white/10 p-4 text-sm text-slate-400">
          No reported vessels are available for this slick.
        </div>
      ) : culprits.map((vessel, index) => {
        const { probability, distanceKm } = mockIndicators(vessel);
        const vesselId = vessel.mmsi == null ? null : String(vessel.mmsi);
        const vesselName = vessel.vessel_name?.trim() || vesselId || `Reported vessel ${vessel.id}`;

        return (
          <div
            key={vessel.id}
            className="rounded-md border border-white/10 p-3"
            onMouseEnter={() => onVesselHover(vesselId)}
            onMouseLeave={() => onVesselHover(null)}
          >
            <div className="flex flex-wrap items-start gap-3">
              <span className="pt-2 font-mono text-xs text-slate-500">#{index + 1}</span>
              <ShipIcon size={18} strokeWidth={1.5} className="mt-2 shrink-0 text-slate-400" />
              <button
                type="button"
                disabled={!vesselId}
                className="min-w-0 flex-1 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 disabled:cursor-default"
                onFocus={() => onVesselHover(vesselId)}
                onBlur={() => onVesselHover(null)}
                onClick={() => vesselId && onVesselSelect(vesselId)}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium text-slate-100">{vesselName}</span>
                  <span className="shrink-0 text-xs text-slate-400">{probability}%*</span>
                </div>
                <div className="mt-1 flex justify-between gap-2 text-xs text-slate-400">
                  <span className="truncate">{vessel.imo ? `IMO ${vessel.imo}` : 'Type unavailable'}</span>
                  <span className="shrink-0">{distanceKm.toFixed(1)} km*</span>
                </div>
                {vessel.source_title && (
                  <p className="mt-1 truncate text-xs text-slate-500" title={vessel.source_title}>
                    {vessel.source_title}
                  </p>
                )}
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/10">
                  <div
                    className={`h-full rounded-full ${probabilityColor(probability)}`}
                    style={{ width: `${probability}%` }}
                  />
                </div>
              </button>
              {vesselId && <VesselIdentityDetails mmsi={vesselId} detectionDate={detectionDate} />}
            </div>
          </div>
        );
      })}
    </div>
  );
}
