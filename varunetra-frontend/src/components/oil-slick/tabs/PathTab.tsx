'use client';

import { Pause, Play } from 'lucide-react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import { useTrajectoryPlayback } from '@/hooks/useTrajectoryPlayback';
import type { Slick } from '@/types/slick';

interface PathTabProps {
  map: MapLibreMap | null;
  slick: Slick;
}

export default function PathTab({ map, slick }: PathTabProps) {
  const playback = useTrajectoryPlayback(map, slick);
  const togglePlayback = () => {
    if (playback.isPlaying) {
      playback.setIsPlaying(false);
      return;
    }
    if (playback.progress >= 1) playback.setProgress(0);
    playback.setIsPlaying(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-widest text-slate-500">Trajectory playback</p>
          <p className="mt-1 text-xs text-slate-400">T−18h to now</p>
        </div>
        <button
          type="button"
          aria-label={playback.isPlaying ? 'Pause trajectory playback' : 'Play trajectory playback'}
          className="flex h-9 w-9 items-center justify-center rounded-md border border-white/10 text-orange-400 transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"
          onClick={togglePlayback}
        >
          {playback.isPlaying ? <Pause size={18} strokeWidth={1.5} /> : <Play size={18} strokeWidth={1.5} />}
        </button>
      </div>
      <div>
        <input
          aria-label="Trajectory time"
          className="w-full accent-orange-400"
          max={100}
          min={0}
          type="range"
          value={Math.round(playback.progress * 100)}
          onChange={event => playback.setProgress(Number(event.currentTarget.value) / 100)}
        />
        <div className="flex justify-between font-mono text-[10px] text-slate-500">
          <span>T−18h</span><span>NOW</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="mr-auto font-mono text-[11px] uppercase tracking-widest text-slate-500">Playback speed</span>
        {([1, 2, 4] as const).map(value => (
          <button
            key={value}
            type="button"
            aria-pressed={playback.speed === value}
            className={`h-9 min-w-10 rounded-md border border-white/10 px-3 text-xs transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 ${
              playback.speed === value ? 'text-orange-400' : 'text-slate-400'
            }`}
            onClick={() => playback.setSpeed(value)}
          >
            {value}x
          </button>
        ))}
      </div>
      <p className="text-xs leading-5 text-slate-400">
        Illustrative trajectory preview. Time-stamped slick movement data will appear here when available.
      </p>
    </div>
  );
}
