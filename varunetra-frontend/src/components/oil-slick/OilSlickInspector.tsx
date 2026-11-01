'use client';

import { useEffect, useState } from 'react';
import { Anchor, Clock3, Info, Map as MapIcon, Share2, X } from 'lucide-react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import AgeTab from '@/components/oil-slick/tabs/AgeTab';
import InfoTab from '@/components/oil-slick/tabs/InfoTab';
import PathTab from '@/components/oil-slick/tabs/PathTab';
import ShipsTab from '@/components/oil-slick/tabs/ShipsTab';
import type { ReportedCulprit, Slick } from '@/types/slick';

export type InspectorTab = 'Info' | 'Age' | 'Path' | 'Ships';

interface OilSlickInspectorProps {
  slick: Slick | null;
  selectedSlickData: Record<string, unknown> | null;
  map: MapLibreMap | null;
  activeTab: InspectorTab;
  onActiveTabChange: (tab: InspectorTab) => void;
  onClose: () => void;
  onVesselHover: (id: string | null) => void;
  onVesselSelect: (id: string) => void;
}

const tabs: Array<{ id: InspectorTab; icon: typeof Info }> = [
  { id: 'Info', icon: Info },
  { id: 'Age', icon: Clock3 },
  { id: 'Path', icon: MapIcon },
  { id: 'Ships', icon: Anchor },
];

export default function OilSlickInspector({
  slick,
  selectedSlickData,
  map,
  activeTab,
  onActiveTabChange,
  onClose,
  onVesselHover,
  onVesselSelect,
}: OilSlickInspectorProps) {
  const [shareLabel, setShareLabel] = useState('Share');
  const [culprits, setCulprits] = useState<ReportedCulprit[]>([]);
  const [isLoadingShips, setIsLoadingShips] = useState(false);
  const [culpritError, setCulpritError] = useState<string | null>(null);
  const spillId = String(selectedSlickData?.spill_id ?? slick?.id ?? '');

  useEffect(() => {
    if (activeTab !== 'Ships' || !spillId) return;

    const controller = new AbortController();
    const loadCulprits = async () => {
      setIsLoadingShips(true);
      setCulprits([]);
      setCulpritError(null);

      try {
        const configuredApiUrl = process.env.NEXT_PUBLIC_VARUNETRA_API_URL;
        if (!configuredApiUrl) {
          throw new Error('NEXT_PUBLIC_VARUNETRA_API_URL is not configured.');
        }

        const endpoint = new URL(configuredApiUrl, window.location.origin);
        const apiPath = endpoint.pathname
          .replace(/\/+$/, '')
          .replace(/\/api\/v1\/(?:slicks|spills)$/, '');
        endpoint.pathname = `${apiPath}/api/v1/spills/${encodeURIComponent(spillId)}/culprits`;
        endpoint.search = '';

        const response = await fetch(endpoint, { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`Failed to load reported vessels (${response.status} ${response.statusText})`);
        }
        const payload: unknown = await response.json();
        if (!Array.isArray(payload)) {
          throw new Error('Reported vessels API returned an invalid response.');
        }

        const parsedCulprits = payload.map((value: unknown) => {
          if (typeof value !== 'object' || value === null) {
            throw new Error('Reported vessels API returned an invalid vessel record.');
          }
          const record = value as Record<string, unknown>;
          if (typeof record.id !== 'number' || typeof record.spill_id !== 'string') {
            throw new Error('Reported vessels API returned a vessel without an id or spill_id.');
          }
          if (record.mmsi !== null && record.mmsi !== undefined && typeof record.mmsi !== 'number') {
            throw new Error('Reported vessels API returned an invalid MMSI.');
          }

          return {
            id: record.id,
            spill_id: record.spill_id,
            mmsi: typeof record.mmsi === 'number' ? record.mmsi : null,
            vessel_name: typeof record.vessel_name === 'string' ? record.vessel_name : null,
            imo: typeof record.imo === 'string' ? record.imo : null,
            source_title: typeof record.source_title === 'string' ? record.source_title : null,
            source_url: typeof record.source_url === 'string' ? record.source_url : null,
            report_date: typeof record.report_date === 'string' ? record.report_date : null,
            notes: typeof record.notes === 'string' ? record.notes : null,
          };
        });
        if (!controller.signal.aborted) setCulprits(parsedCulprits);
      } catch (error) {
        if (controller.signal.aborted) return;
        setCulprits([]);
        setCulpritError(error instanceof Error ? error.message : 'Unable to load reported vessels.');
      } finally {
        if (!controller.signal.aborted) setIsLoadingShips(false);
      }
    };

    void loadCulprits();
    return () => controller.abort();
  }, [activeTab, spillId]);

  const shareSlick = async () => {
    if (!slick) return;
    const shareData = { title: `Oil slick ${slick.id}`, text: `VARUNETRA oil slick ${slick.id}` };
    if (navigator.share) {
      await navigator.share(shareData);
    } else {
      await navigator.clipboard.writeText(`${shareData.text} · ${window.location.href}`);
      setShareLabel('Copied');
      window.setTimeout(() => setShareLabel('Share'), 1500);
    }
  };

  const exportSlick = () => {
    if (!slick) return;
    const blob = new Blob([JSON.stringify(slick.properties, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `oil-slick-${slick.id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <aside
      aria-label="Oil slick inspector"
      aria-hidden={!slick}
      inert={!slick}
      className={`fixed bottom-0 right-0 z-30 flex max-h-[70vh] w-full flex-col overflow-hidden rounded-t-xl border border-white/10 bg-[#080b12]/90 font-mono text-white/55 backdrop-blur-md transition-transform duration-300 ease-out md:top-0 md:h-full md:max-h-none md:w-[420px] md:rounded-none md:rounded-l-md md:border-b-0 md:border-r-0 md:border-t-0 ${
        slick ? 'translate-y-0 md:translate-x-0' : 'translate-y-full md:translate-x-full'
      }`}
    >
      <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-slate-500 md:hidden" aria-hidden="true" />
      <header className="flex shrink-0 items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
        <div className="min-w-0">
          <p className="font-mono text-[11px] uppercase tracking-widest text-orange-400">OIL SLICK</p>
          <p className="mt-1 truncate font-mono text-xs text-slate-400">{slick?.id ?? ''}</p>
        </div>
        <button
          type="button"
          aria-label="Close oil slick inspector"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-white/10 text-slate-400 transition-colors hover:bg-white/5 hover:text-orange-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"
          onClick={onClose}
        >
          <X size={18} strokeWidth={1.5} />
        </button>
      </header>

      <div role="tablist" aria-label="Oil slick details" className="grid shrink-0 grid-cols-4 border-b border-white/10 px-3">
        {tabs.map(({ id, icon: Icon }) => (
          <button
            key={id}
            id={`slick-tab-${id.toLowerCase()}`}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            aria-controls="slick-tab-content"
            className={`flex h-12 items-center justify-center gap-2 border-b-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-400 ${
              activeTab === id ? 'border-orange-400 text-orange-400' : 'border-transparent text-slate-400 hover:text-slate-100'
            }`}
            onClick={() => onActiveTabChange(id)}
          >
            <Icon size={18} strokeWidth={1.5} className={activeTab === id ? 'text-orange-400' : 'text-slate-400'} />
            {id}
          </button>
        ))}
      </div>

      <div
        id="slick-tab-content"
        role="tabpanel"
        aria-labelledby={`slick-tab-${activeTab.toLowerCase()}`}
        className="min-h-0 flex-1 overflow-y-auto px-5 py-5"
      >
        {slick && activeTab === 'Info' && <InfoTab slick={slick} selectedSlickData={selectedSlickData} />}
        {slick && activeTab === 'Age' && <AgeTab slick={slick} />}
        {slick && activeTab === 'Path' && <PathTab map={map} slick={slick} />}
        {slick && activeTab === 'Ships' && (
          <ShipsTab
            culprits={culprits}
            detectionDate={slick?.detectedAt ?? null}
            isLoading={isLoadingShips}
            error={culpritError}
            onVesselHover={onVesselHover}
            onVesselSelect={onVesselSelect}
          />
        )}
      </div>

      <footer className="flex shrink-0 gap-2 border-t border-white/10 px-5 py-3">
        <button
          type="button"
          className="flex h-9 flex-1 items-center justify-center gap-2 rounded-md border border-white/10 text-xs text-slate-300 transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"
          onClick={exportSlick}
          disabled={!slick}
        >
          Export
        </button>
        <button
          type="button"
          aria-label={shareLabel === 'Share' ? 'Share oil slick' : 'Oil slick link copied'}
          className="flex h-9 flex-1 items-center justify-center gap-2 rounded-md border border-white/10 text-xs text-slate-300 transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"
          onClick={() => void shareSlick()}
          disabled={!slick}
        >
          <Share2 size={18} strokeWidth={1.5} /> {shareLabel}
        </button>
      </footer>
    </aside>
  );
}
