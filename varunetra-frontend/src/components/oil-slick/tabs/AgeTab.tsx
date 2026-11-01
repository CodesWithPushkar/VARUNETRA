import type { AgeEstimate, Slick } from '@/types/slick';

interface AgeTabProps {
  slick: Slick;
}

export default function AgeTab({ slick }: AgeTabProps) {
  // TODO: Replace these environmental estimates with the backend age-analysis payload.
  const hours = slick.ageHours ?? 12;
  const uncertainty = slick.ageSigmaHours ?? 3;
  const estimate: AgeEstimate = {
    hours,
    uncertaintyHours: uncertainty,
    confidenceBand: [Math.max(0, hours - uncertainty), hours + uncertainty],
    windSpeedKnots: 8.4,
    spreadRateKm2PerHour: 0.18,
    shape: 'Elongated',
  };
  const rangeMax = Math.max(estimate.confidenceBand[1] * 1.4, 1);
  const rangeStart = (estimate.confidenceBand[0] / rangeMax) * 100;
  const rangeWidth = ((estimate.confidenceBand[1] - estimate.confidenceBand[0]) / rangeMax) * 100;

  return (
    <div className="space-y-6">
      <section className="text-center">
        <p className="font-mono text-[11px] uppercase tracking-widest text-slate-500">Estimated slick age</p>
        <p className="mt-3 text-5xl font-semibold tabular-nums text-amber-400">{estimate.hours.toFixed(1)}h</p>
        <p className="mt-2 text-sm text-slate-400">
          ± {estimate.uncertaintyHours.toFixed(1)}h · {estimate.confidenceBand[0].toFixed(1)}–{estimate.confidenceBand[1].toFixed(1)}h range
        </p>
      </section>
      <div aria-label="Estimated age confidence band" className="relative h-2 rounded-full bg-white/10">
        <div
          className="absolute top-0 h-2 rounded-full bg-amber-400"
          style={{ left: `${rangeStart}%`, width: `${Math.min(rangeWidth, 100 - rangeStart)}%` }}
        />
      </div>
      <div>
        {[
          ['Wind speed', `${estimate.windSpeedKnots.toFixed(1)} kn`],
          ['Spread rate', `${estimate.spreadRateKm2PerHour.toFixed(2)} km²/hr`],
          ['Slick shape', estimate.shape],
        ].map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-4 border-b border-white/10 py-3">
            <span className="text-xs text-slate-400">{label}</span>
            <span className="text-sm text-slate-100">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
