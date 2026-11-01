import Link from 'next/link';
import { API_GROUPS, ENDPOINT_COUNT } from './apiCatalog';

export default function DocsClient() {
  return (
    <main className="min-h-screen bg-[#06060c] px-5 py-10 text-white sm:px-8">
      <div className="mx-auto max-w-4xl">
        <Link href="/" className="font-mono text-xs tracking-[0.2em] text-cyan-300/80 hover:text-cyan-200">
          VARUNETRA · MARINE TRACKING
        </Link>
        <h1 className="mt-8 text-3xl font-semibold tracking-wide">Documentation &amp; API</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-white/60">
          The map combines Sentinel-1 SAR imagery, an instanced AIS vessel layer, global wind streamlines,
          hydrodynamic current vectors, and an oil-slick GeoJSON overlay.
        </p>

        <section className="mt-10">
          <h2 className="font-mono text-xs tracking-[0.18em] text-white/40">{ENDPOINT_COUNT} ACTIVE API ROUTES</h2>
          {API_GROUPS.map(group => (
            <div key={group.id} className="mt-4">
              <h3 className="text-lg font-medium">{group.title}</h3>
              <p className="mt-1 text-sm text-white/50">{group.blurb}</p>
              <div className="mt-4 grid gap-3">
                {group.endpoints.map(endpoint => (
                  <article key={endpoint.path} className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-emerald-400/10 px-2 py-1 font-mono text-[10px] text-emerald-300">
                        {Array.isArray(endpoint.method) ? endpoint.method.join(' / ') : endpoint.method}
                      </span>
                      <code className="break-all font-mono text-xs text-cyan-100">{endpoint.path}</code>
                    </div>
                    <p className="mt-3 text-sm text-white/75">{endpoint.summary}</p>
                    {endpoint.notes && <p className="mt-2 text-xs leading-5 text-white/45">{endpoint.notes}</p>}
                  </article>
                ))}
              </div>
            </div>
          ))}
        </section>

        <section className="mt-10 rounded-lg border border-white/10 bg-white/[0.025] p-4">
          <h2 className="font-medium">Data placeholders</h2>
          <p className="mt-2 text-sm leading-6 text-white/55">
            AIS and ocean-current feeds are not connected yet. Their route folders, <code>/api/ais-stream</code> and
            <code> /api/ocean-currents</code>, are intentionally empty for future integrations.
          </p>
        </section>
      </div>
    </main>
  );
}
