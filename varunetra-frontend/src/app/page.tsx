import VarunetraMap from '@/components/VarunetraMap';

export default function Home() {
  return (
    <main className="relative h-[100dvh] w-full overflow-hidden bg-[#06060c] text-white">
      <VarunetraMap />
      <header className="pointer-events-none absolute left-4 top-4 z-10 rounded border border-white/10 bg-[#080b12]/90 px-4 py-3 shadow-lg backdrop-blur-md sm:left-6 sm:top-6">
        <p className="font-mono text-[10px] tracking-[0.24em] text-cyan-300/70">VARUNETRA · MARINE ENVIRONMENT TRACKING</p>
        <h1 className="mt-1 text-sm font-medium tracking-[0.12em] text-white sm:text-base">OCEAN OBSERVATION</h1>
        <p className="mt-1 font-mono text-[9px] tracking-[0.12em] text-white/45">SENTINEL-1 SAR · AIS · WIND · CURRENTS</p>
      </header>
    </main>
  );
}
