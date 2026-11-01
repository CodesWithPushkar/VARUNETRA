import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = {
  title: 'Privacy',
  description: 'How the VARUNETRA marine map loads map, wind, and Sentinel-1 tile data.',
  alternates: { canonical: '/privacy' },
};

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-[#06060c] px-5 py-10 text-white sm:px-8">
      <div className="mx-auto max-w-3xl">
        <Link href="/" className="font-mono text-xs tracking-[0.2em] text-cyan-300/80 hover:text-cyan-200">
          VARUNETRA · MARINE TRACKING
        </Link>
        <h1 className="mt-8 text-3xl font-semibold">Privacy</h1>
        <p className="mt-3 text-sm leading-6 text-white/60">
          The marine map does not request or follow your device location. Browser geolocation is disabled by the
          site’s permissions policy.
        </p>

        <section className="mt-8 space-y-5 text-sm leading-6 text-white/65">
          <div>
            <h2 className="font-medium text-white">Map and weather data</h2>
            <p className="mt-1">
              Carto Dark Matter resources are loaded through this application’s tile proxy. Global wind fields are
              requested from the application’s wind route, which fetches NOAA GFS data.
            </p>
          </div>
          <div>
            <h2 className="font-medium text-white">Sentinel-1 imagery</h2>
            <p className="mt-1">
              SAR tiles are requested through the application’s Sentinel tile proxy. When configured, the server
              forwards tile requests to the HTTPS URL template in <code>SENTINEL_1_TILE_URL</code>. Any provider
              credentials remain server-side.
            </p>
          </div>
          <div>
            <h2 className="font-medium text-white">AIS and ocean currents</h2>
            <p className="mt-1">
              These are frontend rendering placeholders and are not connected to a data service yet. No AIS or
              ocean-current feed is requested by the current application.
            </p>
          </div>
          <div>
            <h2 className="font-medium text-white">Browser storage</h2>
            <p className="mt-1">
              The marine map does not store a location, AIS history, or ocean-current data in browser storage.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
