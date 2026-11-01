import type { Metadata } from 'next';
import DocsClient from './DocsClient';
import { ENDPOINT_COUNT } from './apiCatalog';

export const metadata: Metadata = {
  title: 'Marine Map Documentation & API Reference',
  description: `VARUNETRA marine map documentation and reference for ${ENDPOINT_COUNT} active routes covering Carto map tiles, NOAA GFS wind fields, and Sentinel-1 SAR tiles.`,
  alternates: { canonical: '/docs' },
  openGraph: {
    title: 'VARUNETRA — Marine Map Documentation',
    description: `Marine map setup and API reference for ${ENDPOINT_COUNT} active routes.`,
    url: '/docs',
    type: 'article',
  },
};

export default function DocsPage() {
  return <DocsClient />;
}
