import { useEffect, useState } from 'react';
import type { Feature, FeatureCollection, LineString, Point } from 'geojson';
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl';
import type { Slick } from '@/types/slick';

const LINE_SOURCE = 'slick-trajectory-line';
const DOTS_SOURCE = 'slick-trajectory-dots';
const CURRENT_SOURCE = 'slick-trajectory-current';

function trajectoryCoordinates([longitude, latitude]: [number, number]): [number, number][] {
  // TODO: Replace the illustrative route with time-stamped trajectory data for this slick.
  return [
    [longitude - 0.08, latitude - 0.035],
    [longitude - 0.045, latitude - 0.01],
    [longitude - 0.015, latitude + 0.012],
    [longitude + 0.02, latitude + 0.006],
    [longitude + 0.05, latitude + 0.03],
    [longitude + 0.08, latitude + 0.02],
  ];
}

export function useTrajectoryPlayback(map: MapLibreMap | null, slick: Slick | null) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(1);
  const [speed, setSpeed] = useState<1 | 2 | 4>(1);

  useEffect(() => {
    if (!map || !slick || !map.isStyleLoaded()) return;

    const coordinates = trajectoryCoordinates(slick.center);
    const line: Feature<LineString> = {
      type: 'Feature',
      properties: {},
      geometry: { type: 'LineString', coordinates },
    };
    const dots: FeatureCollection<Point> = {
      type: 'FeatureCollection',
      features: coordinates.map((coordinate, index) => ({
        type: 'Feature',
        properties: { index },
        geometry: { type: 'Point', coordinates: coordinate },
      })),
    };
    const currentPoint: Feature<Point> = {
      type: 'Feature',
      properties: {},
      geometry: { type: 'Point', coordinates: coordinates[coordinates.length - 1] },
    };

    map.addSource(LINE_SOURCE, { type: 'geojson', data: line });
    map.addSource(DOTS_SOURCE, { type: 'geojson', data: dots });
    map.addSource(CURRENT_SOURCE, { type: 'geojson', data: currentPoint });
    map.addLayer({
      id: LINE_SOURCE,
      type: 'line',
      source: LINE_SOURCE,
      paint: { 'line-color': '#22d3ee', 'line-width': 2, 'line-dasharray': [2, 2] },
    });
    map.addLayer({
      id: DOTS_SOURCE,
      type: 'circle',
      source: DOTS_SOURCE,
      paint: { 'circle-color': '#22d3ee', 'circle-radius': 3, 'circle-opacity': 0.8 },
    });
    map.addLayer({
      id: CURRENT_SOURCE,
      type: 'circle',
      source: CURRENT_SOURCE,
      paint: { 'circle-color': '#22d3ee', 'circle-radius': 5, 'circle-stroke-color': '#e2e8f0', 'circle-stroke-width': 1.5 },
    });

    return () => {
      [CURRENT_SOURCE, DOTS_SOURCE, LINE_SOURCE].forEach(id => {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
      });
    };
  }, [map, slick]);

  useEffect(() => {
    if (!map || !slick || !map.getSource(CURRENT_SOURCE)) return;
    const coordinates = trajectoryCoordinates(slick.center);
    const scaledIndex = Math.min(progress, 1) * (coordinates.length - 1);
    const index = Math.floor(scaledIndex);
    const fraction = scaledIndex - index;
    const start = coordinates[index];
    const end = coordinates[Math.min(index + 1, coordinates.length - 1)];
    const point: [number, number] = [
      start[0] + (end[0] - start[0]) * fraction,
      start[1] + (end[1] - start[1]) * fraction,
    ];
    const source = map.getSource(CURRENT_SOURCE) as GeoJSONSource;
    source.setData({
      type: 'Feature',
      properties: {},
      geometry: { type: 'Point', coordinates: point },
    });
  }, [map, slick, progress]);

  useEffect(() => {
    if (!isPlaying) return;
    const timer = window.setInterval(() => {
      setProgress(value => {
        const next = value + 0.005 * speed;
        if (next >= 1) {
          setIsPlaying(false);
          return 0;
        }
        return next;
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, [isPlaying, speed]);

  return { isPlaying, setIsPlaying, progress, setProgress, speed, setSpeed };
}
