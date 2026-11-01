'use client';

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import type { Slick } from '@/types/slick';

interface InfoTabProps {
  slick: Slick;
  selectedSlickData: Record<string, unknown> | null;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function asNumber(value: unknown): number | null {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return null;
  return Number(value);
}

function asString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function firstPresent(properties: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = properties[key];
    if (value !== null && value !== undefined && value !== '') return value;
  }
  return null;
}

export default function InfoTab({ slick, selectedSlickData }: InfoTabProps) {
  const [copied, setCopied] = useState(false);
  const properties = { ...slick.properties, ...selectedSlickData };
  const areaKm2 = asNumber(firstPresent(properties, 'area_km2', 'areaKm2', 'area')) ?? slick.areaKm2;
  const confidenceValue = asNumber(firstPresent(
    properties,
    'detection_confidence',
    'detectionConfidence',
    'confidence'
  )) ?? slick.confidence;
  const detectedAt = asString(firstPresent(
    properties,
    'image_time_utc',
    'imageTimeUtc',
    'timestamp'
  )) ?? slick.detectedAt;
  const originLat = asNumber(firstPresent(properties, 'origin_lat', 'originLat')) ?? slick.originLat;
  const originLon = asNumber(firstPresent(properties, 'origin_lon', 'originLon')) ?? slick.originLon;
  const originRadiusKm = asNumber(firstPresent(
    properties,
    'origin_radius_km',
    'originRadiusKm'
  )) ?? slick.originRadiusKm;
  const sceneId = asString(firstPresent(properties, 'scene_name', 'sceneName')) ?? slick.sceneId;
  const ageHours = asNumber(firstPresent(properties, 'age_hours', 'ageHours', 'age_estimate')) ?? slick.ageHours;
  const coordinates = originLat != null && originLon != null
    ? `${originLat.toFixed(4)}, ${originLon.toFixed(4)}`
    : 'Unavailable';
  const confidence = confidenceValue == null
    ? 'Unavailable'
    : `${(confidenceValue * 100).toFixed(1)}%`;

  const copySceneId = async () => {
    if (!sceneId) return;
    await navigator.clipboard.writeText(sceneId);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  const rows: Array<[string, string]> = [
    ['Area km²', areaKm2 == null ? 'Unavailable' : areaKm2.toFixed(2)],
    ['Confidence', confidence],
    ['Date detected', formatDate(detectedAt)],
    ['Origin coordinates', coordinates],
    ['Origin radius', originRadiusKm == null ? 'Unavailable' : `${originRadiusKm.toFixed(2)} km`],
  ];

  return (
    <div className="space-y-5">
      <div>
        {rows.map(([label, value]) => (
          <div key={label} className="grid grid-cols-[minmax(110px,0.8fr)_minmax(0,1.2fr)] gap-3 border-b border-white/10 py-3">
            <span className="text-xs text-slate-400">{label}</span>
            <span className="break-words text-right text-sm text-slate-100">{value}</span>
          </div>
        ))}
        <div className="grid grid-cols-[minmax(110px,0.8fr)_minmax(0,1.2fr)] items-start gap-3 border-b border-white/10 py-3">
          <span className="text-xs text-slate-400">Scene ID</span>
          <div className="flex min-w-0 items-start justify-end gap-2">
            <span className="min-w-0 break-all text-right text-sm text-slate-100">{sceneId || 'Unavailable'}</span>
            {sceneId && (
              <button
                type="button"
                aria-label={copied ? 'Scene ID copied' : 'Copy scene ID'}
                title={copied ? 'Copied' : 'Copy scene ID'}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-white/10 text-slate-400 transition-colors hover:bg-white/5 hover:text-orange-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"
                onClick={() => void copySceneId()}
              >
                {copied ? <Check size={18} strokeWidth={1.5} /> : <Copy size={18} strokeWidth={1.5} />}
              </button>
            )}
          </div>
        </div>
      </div>
      <section className="rounded-md border border-amber-400/20 bg-amber-400/5 p-4">
        <p className="font-mono text-[11px] uppercase tracking-widest text-slate-500">Estimated age</p>
        <p className="mt-2 text-3xl font-semibold tabular-nums text-amber-400">
          {ageHours == null ? '—' : `${ageHours.toFixed(1)} hours`}
        </p>
        {slick.ageSigmaHours != null && (
          <p className="mt-1 text-xs text-slate-400">± {slick.ageSigmaHours.toFixed(1)} hours</p>
        )}
      </section>
    </div>
  );
}
