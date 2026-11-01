'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import { Anchor, Globe2, Layers3, RotateCw, Satellite, Ship, Waves, Wind } from 'lucide-react';
import { createAisShipLayer, packShip, rgb } from '@/lib/ais-gl-layer';
import { CurrentsOverlay, type OceanCurrentGrid } from '@/lib/currents-gl-layer';
import { loadMercatorCurrents } from '@/lib/mercator-currents';
import OilSlickInspector, { type InspectorTab } from '@/components/oil-slick/OilSlickInspector';
import WindBar from '@/components/WindBar';
import { useSelectedSlick } from '@/hooks/useSelectedSlick';
import type { Slick, Vessel } from '@/types/slick';
import 'maplibre-gl/dist/maplibre-gl.css';

function spillOverviewPosition(coordinates: unknown): [number, number] | null {
  let minLongitude = Infinity;
  let minLatitude = Infinity;
  let maxLongitude = -Infinity;
  let maxLatitude = -Infinity;

  const visit = (value: unknown): void => {
    if (!Array.isArray(value)) return;
    const [longitude, latitude] = value;
    if (typeof longitude === 'number' && typeof latitude === 'number') {
      minLongitude = Math.min(minLongitude, longitude);
      minLatitude = Math.min(minLatitude, latitude);
      maxLongitude = Math.max(maxLongitude, longitude);
      maxLatitude = Math.max(maxLatitude, latitude);
      return;
    }
    value.forEach(visit);
  };

  visit(coordinates);
  if (!Number.isFinite(minLongitude) || !Number.isFinite(minLatitude)) return null;
  return [(minLongitude + maxLongitude) / 2, (minLatitude + maxLatitude) / 2];
}

function distanceBetweenKm(from: [number, number], to: [number, number]): number {
  const radians = (degrees: number) => degrees * Math.PI / 180;
  const [longitude1, latitude1] = from.map(radians);
  const [longitude2, latitude2] = to.map(radians);
  const latitudeDelta = latitude2 - latitude1;
  const longitudeDelta = longitude2 - longitude1;
  const haversine =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(latitude1) * Math.cos(latitude2) * Math.sin(longitudeDelta / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

function nearbyVessels(center: [number, number], vessels: Vessel[]): Vessel[] {
  if (vessels.length === 0) {
    // TODO: Remove these sample vessels when the nearby-vessel endpoint is wired to the inspector.
    return [
      { id: 'mock-311000101', name: 'MV Ocean Pioneer', shipType: 'Cargo', distanceKm: 3.2, probability: 68, position: [center[0] + 0.03, center[1] + 0.02] },
      { id: 'mock-311000102', name: 'MT Blue Horizon', shipType: 'Tanker', distanceKm: 7.8, probability: 42, position: [center[0] - 0.06, center[1] + 0.03] },
      { id: 'mock-311000103', name: 'FV Coastal Star', shipType: 'Fishing', distanceKm: 12.4, probability: 18, position: [center[0] + 0.09, center[1] - 0.04] },
    ];
  }

  return vessels
    .map(vessel => {
      const distanceKm = distanceBetweenKm(center, vessel.position);
      // TODO: Replace this illustrative ranking with backend attribution scores.
      const probability = Math.max(1, Math.min(99, Math.round(100 / (1 + distanceKm / 4))));
      return { ...vessel, distanceKm, probability };
    })
    .sort((left, right) => left.distanceKm - right.distanceKm)
    .slice(0, 10);
}

export interface AisShipPosition {
  lng: number;
  lat: number;
  course?: number;
  color?: string;
}

interface VarunetraMapProps {
  aisShips?: AisShipPosition[];
}

export default function VarunetraMap({ aisShips = [] }: VarunetraMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const aisLayerRef = useRef<ReturnType<typeof createAisShipLayer> | null>(null);
  const currentsRef = useRef<CurrentsOverlay | null>(null);
  const projectionInitializedRef = useRef(false);
  const showShipsRef = useRef(false);
  const refreshAisVesselsRef = useRef<(() => Promise<void>) | null>(null);
  const aisRequestRef = useRef<AbortController | null>(null);
  const aisFrameRef = useRef<number | null>(null);
  const liveVesselsRef = useRef<Vessel[]>([]);
  const selectedSlickRef = useRef<Slick | null>(null);
  const { slick: selectedSlick, openSlick, closeSlick } = useSelectedSlick(mapRef);
  const [mapReady, setMapReady] = useState(false);
  const [mapInstance, setMapInstance] = useState<maplibregl.Map | null>(null);
  const [windEnabled, setWindEnabled] = useState(true);
  const [currentsEnabled, setCurrentsEnabled] = useState(false);
  const [currentField, setCurrentField] = useState<OceanCurrentGrid | null>(null);
  const [currentsStatus, setCurrentsStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [currentsError, setCurrentsError] = useState('');
  const mercatorKey = process.env.NEXT_PUBLIC_MERCATOR_API_KEY ?? '';
  const [satelliteEnabled, setSatelliteEnabled] = useState(false);
  const [view3D, setView3D] = useState(true);
  const [globeRotationEnabled, setGlobeRotationEnabled] = useState(true);
  const [showShips, setShowShips] = useState(false);
  const [showStoppedShips, setShowStoppedShips] = useState(false);
  const [isSpillClustered, setIsSpillClustered] = useState(true);
  const [activeSlickTab, setActiveSlickTab] = useState<InspectorTab>('Info');
  const [isFetchingShips, setIsFetchingShips] = useState(false);
  const [isFetchingSpills, setIsFetchingSpills] = useState(false);
  const [inspectorVessels, setInspectorVessels] = useState<Vessel[]>([]);
  const [selectedSlickData, setSelectedSlickData] = useState<Record<string, unknown> | null>(null);

  const handleVesselHover = useCallback((id: string | null) => {
    const map = mapRef.current;
    if (!map || !map.getLayer('ais-inspector-highlight')) return;
    map.setFilter('ais-inspector-highlight', ['==', ['to-string', ['get', 'mmsi']], id ?? '']);
  }, []);

  const handleVesselSelect = useCallback((id: string) => {
    const vessel = inspectorVessels.find(candidate => candidate.id === id);
    if (!vessel) return;
    handleVesselHover(id);
    const map = mapRef.current;
    map?.flyTo({
      center: vessel.position,
      zoom: Math.max(map.getZoom(), 8),
      padding: window.matchMedia('(max-width: 767px)').matches
        ? { top: 0, right: 0, bottom: Math.round(window.innerHeight * 0.35), left: 0 }
        : { top: 0, right: 420, bottom: 0, left: 0 },
      duration: 700,
    });
  }, [handleVesselHover, inspectorVessels]);

  const handleInspectorClose = useCallback(() => {
    selectedSlickRef.current = null;
    setSelectedSlickData(null);
    setInspectorVessels([]);
    setActiveSlickTab('Info');
    handleVesselHover(null);
    closeSlick();
  }, [closeSlick, handleVesselHover]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const container = containerRef.current;
    maplibregl.setWorkerUrl(`/vendor/maplibre/${maplibregl.getVersion()}/maplibre-gl-worker.mjs`);
    const map = new maplibregl.Map({
      container,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: [72.5, 18.5],
      zoom: 8,
      pitch: 30,
      minZoom: 0,
      maxZoom: 22,
      transformRequest: url => {
        const requestUrl = new URL(url, window.location.origin);
        if (requestUrl.protocol === 'https:' &&
            (requestUrl.hostname === 'basemaps.cartocdn.com' || requestUrl.hostname.endsWith('.basemaps.cartocdn.com'))) {
          return { url: `/api/proxy-tiles?url=${encodeURIComponent(requestUrl.toString())}` };
        }
        return { url };
      },
    });
    mapRef.current = map;

    let activeSlickRequest: AbortController | null = null;
    let spillLoadingTimer: number | null = null;
    let slickAnimationFrame: number | null = null;
    let handleAisViewportChange: (() => void) | null = null;
    let aisDebounceTimer: number | null = null;
    let vesselPollTimer: number | null = null;

    map.on('load', () => {
      map.setProjection({ type: 'globe' });
      map.setPadding({
        top: 0,
        right: 0,
        bottom: Math.round(container.clientHeight * 0.1),
        left: 0,
      });
      map.addSource('satellite-imagery', {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        maxzoom: 19,
        attribution: 'Esri, Maxar, Earthstar Geographics',
      });
      map.addLayer({
        id: 'satellite-imagery-layer',
        type: 'raster',
        source: 'satellite-imagery',
        layout: { visibility: 'none' },
        paint: { 'raster-opacity': 1 },
      });
      map.addSource('ais-vessels', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
        tolerance: 0,
        generateId: true,
      });

      const arrowCanvas = document.createElement('canvas');
      arrowCanvas.width = 64;
      arrowCanvas.height = 64;
      const arrowContext = arrowCanvas.getContext('2d');
      if (arrowContext && !map.hasImage('ship-arrow')) {
        arrowContext.fillStyle = '#fff';
        arrowContext.beginPath();
        arrowContext.moveTo(32, 4);
        arrowContext.lineTo(58, 56);
        arrowContext.lineTo(32, 45);
        arrowContext.lineTo(6, 56);
        arrowContext.closePath();
        arrowContext.fill();
        map.addImage('ship-arrow', arrowContext.getImageData(0, 0, 64, 64), { sdf: true });
      }

      map.addLayer({
        id: 'ais-vessels-moving',
        type: 'symbol',
        source: 'ais-vessels',
        filter: ['>', ['get', 'sog'], 0.5],
        layout: {
          'icon-image': 'ship-arrow',
          'icon-size': 0.2,
          'icon-anchor': 'center',
          'icon-rotate': ['coalesce', ['get', 'trueHeading'], ['get', 'cog']],
          'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true,
          'symbol-placement': 'point',
          visibility: 'none',
        },
        paint: {
          'icon-color': ['match', ['get', 'typeCode'], 'cargo', '#34d399', 'tanker', '#7f1d1d', 'passenger', '#60a5fa', 'fishing', '#f59e0b', 'tug', '#fbbf24', 'military', '#c084fc', 'pleasure', '#6ee7b7', 'search_and_rescue', '#fb7185', '#a9b3c2'],
          'icon-opacity': 0.95,
          'icon-halo-color': '#06131d',
          'icon-halo-width': 1,
        },
      });
      map.addLayer({
        id: 'ais-vessels-stopped',
        type: 'circle',
        source: 'ais-vessels',
        filter: ['<=', ['get', 'sog'], 0.5],
        layout: { visibility: 'none' },
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 2.5, 8, 5, 12, 7],
          'circle-color': ['match', ['get', 'typeCode'], 'cargo', '#34d399', 'tanker', '#7f1d1d', 'passenger', '#60a5fa', 'fishing', '#f59e0b', 'tug', '#fbbf24', 'military', '#c084fc', 'pleasure', '#6ee7b7', 'search_and_rescue', '#fb7185', '#a9b3c2'],
          'circle-stroke-color': '#dfeaf9',
          'circle-stroke-width': 0.5,
          'circle-opacity': 0.9,
        },
      });
      map.addLayer({
        id: 'ais-inspector-highlight',
        type: 'circle',
        source: 'ais-vessels',
        filter: ['==', ['to-string', ['get', 'mmsi']], ''],
        paint: {
          'circle-radius': 10,
          'circle-color': 'rgba(34, 211, 238, 0.12)',
          'circle-stroke-color': '#22d3ee',
          'circle-stroke-width': 2,
        },
      });

      map.on('click', 'ais-vessels-moving', event => {
        const feature = event.features?.[0];
        if (!feature) return;
        const props = feature.properties ?? {};
        const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '300px' });
        const content = document.createElement('div');
        content.innerHTML = `
          <div style="font-weight:700; color:#e5f4ff; margin-bottom:6px;">${props.name || `MMSI ${props.mmsi}`}</div>
          <div style="font-size:12px; line-height:1.5; color:#d8e8f7;">MMSI: ${props.mmsi ?? 'N/A'}<br/>Speed: ${props.sog != null ? `${Number(props.sog).toFixed(1)} kn` : 'N/A'}<br/>Destination: ${props.destination || 'N/A'}</div>
        `;
        popup.setLngLat(event.lngLat).setDOMContent(content).addTo(map);
      });

      map.on('click', 'ais-vessels-stopped', event => {
        const feature = event.features?.[0];
        if (!feature) return;
        const props = feature.properties ?? {};
        const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '300px' });
        const content = document.createElement('div');
        content.innerHTML = `
          <div style="font-weight:700; color:#e5f4ff; margin-bottom:6px;">${props.name || `MMSI ${props.mmsi}`}</div>
          <div style="font-size:12px; line-height:1.5; color:#d8e8f7;">MMSI: ${props.mmsi ?? 'N/A'}<br/>Speed: ${props.sog != null ? `${Number(props.sog).toFixed(1)} kn` : 'N/A'}<br/>Destination: ${props.destination || 'N/A'}</div>
        `;
        popup.setLngLat(event.lngLat).setDOMContent(content).addTo(map);
      });

      map.on('mouseenter', 'ais-vessels-moving', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'ais-vessels-moving', () => { map.getCanvas().style.cursor = ''; });
      map.on('mouseenter', 'ais-vessels-stopped', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'ais-vessels-stopped', () => { map.getCanvas().style.cursor = ''; });

      map.addSource('sar-oil-slicks', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
        tolerance: 0,
      });
      map.addSource('oil-slick-overview', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
        cluster: true,
        clusterMaxZoom: 7,
        clusterRadius: 42,
      });
      map.addLayer({
        id: 'oil-slick-clusters',
        type: 'circle',
        source: 'oil-slick-overview',
        maxzoom: 7,
        filter: ['has', 'point_count'],
        layout: { visibility: 'visible' },
        paint: {
          'circle-color': '#ff9900',
          'circle-radius': ['step', ['get', 'point_count'], 9, 10, 12, 50, 16, 100, 20],
          'circle-opacity': 0.9,
          'circle-stroke-color': '#fff0d6',
          'circle-stroke-width': 1.5,
        },
      });
      map.addLayer({
        id: 'oil-slick-cluster-count',
        type: 'symbol',
        source: 'oil-slick-overview',
        maxzoom: 7,
        filter: ['has', 'point_count'],
        layout: {
          visibility: 'visible',
          'text-field': ['get', 'point_count_abbreviated'],
          'text-size': 11,
          'text-allow-overlap': true,
        },
        paint: {
          'text-color': '#211300',
        },
      });
      map.addLayer({
        id: 'oil-slick-overview-single',
        type: 'circle',
        source: 'oil-slick-overview',
        maxzoom: 7,
        filter: ['!', ['has', 'point_count']],
        layout: { visibility: 'visible' },
        paint: {
          'circle-color': '#ff9900',
          'circle-radius': 5,
          'circle-opacity': 0.95,
          'circle-stroke-color': '#fff0d6',
          'circle-stroke-width': 1,
        },
      });
      map.addLayer({
        id: 'sar-oil-slicks-fill',
        type: 'fill',
        source: 'sar-oil-slicks',
        layout: { visibility: 'none' },
        paint: {
          'fill-color': '#FF9900',
          'fill-opacity': 1.0,
        },
      });
      map.addLayer({
        id: 'sar-oil-slicks-outline',
        type: 'line',
        source: 'sar-oil-slicks',
        layout: {
          visibility: 'none',
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': '#FF9900',
          'line-width': 1.5,
          'line-opacity': 1.0,
        },
      });
      const handleSpillClick = (event: maplibregl.MapLayerMouseEvent) => {
        const feature = event.features?.[0];
        const properties = feature?.properties;
        const center = feature && spillOverviewPosition(feature.geometry.type === 'GeometryCollection'
          ? feature.geometry.geometries.map(geometry => geometry.type === 'GeometryCollection' ? [] : geometry.coordinates)
          : feature.geometry.coordinates);
        if (!properties || !center) return;

        const id = String(properties.spill_id ?? properties.id ?? feature.id ?? '');
        if (!id) return;
        setSelectedSlickData(properties);
        const asNumber = (value: unknown) => value == null || value === '' || !Number.isFinite(Number(value))
          ? null
          : Number(value);
        const selected: Slick = {
          id,
          center,
          kind: properties.kind == null ? null : String(properties.kind),
          areaKm2: asNumber(properties.area_km2 ?? properties.area),
          confidence: asNumber(properties.detection_confidence ?? properties.confidence),
          detectedAt: properties.image_time_utc == null && properties.timestamp == null
            ? null
            : String(properties.image_time_utc ?? properties.timestamp),
          ageHours: asNumber(properties.age_hours ?? properties.age_estimate ?? properties.est_age_hr),
          ageSigmaHours: asNumber(properties.age_sigma_hours),
          originLat: asNumber(properties.origin_lat),
          originLon: asNumber(properties.origin_lon),
          originRadiusKm: asNumber(properties.origin_radius_km),
          originMmsi: properties.origin_mmsi == null ? null : String(properties.origin_mmsi),
          sceneId: properties.scene_name == null ? null : String(properties.scene_name),
          dataSource: properties.data_source == null ? null : String(properties.data_source),
          properties: { ...properties, id },
        };
        selectedSlickRef.current = selected;
        setInspectorVessels(nearbyVessels(center, liveVesselsRef.current));
        setActiveSlickTab('Info');
        openSlick(selected);
      };
      ['sar-oil-slicks-fill', 'sar-oil-slicks-outline', 'oil-slick-overview-single'].forEach(layerId => {
        map.on('click', layerId, handleSpillClick);
        map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = ''; });
      });
      map.on('click', event => {
        const hit = map.queryRenderedFeatures(event.point, {
          layers: ['sar-oil-slicks-fill', 'sar-oil-slicks-outline', 'oil-slick-overview-single'],
        });
        if (hit.length || !selectedSlickRef.current) return;
        selectedSlickRef.current = null;
        setSelectedSlickData(null);
        setInspectorVessels([]);
        handleVesselHover(null);
        closeSlick();
      });

      const apiUrl = process.env.NEXT_PUBLIC_VARUNETRA_API_URL;
      const loadSpillsOnce = async () => {
        const requestController = new AbortController();
        activeSlickRequest = requestController;
        spillLoadingTimer = window.setTimeout(() => {
          if (activeSlickRequest === requestController && !requestController.signal.aborted) {
            setIsFetchingSpills(true);
          }
        }, 180);

        try {
          if (!apiUrl) {
            throw new Error('NEXT_PUBLIC_VARUNETRA_API_URL is not configured.');
          }

          const requestUrl = new URL(apiUrl, window.location.origin);
          if (!/\/api\/v1\/(?:slicks|spills)\/?$/.test(requestUrl.pathname)) {
            requestUrl.pathname = `${requestUrl.pathname.replace(/\/+$/, '')}/api/v1/slicks`;
          }

          const response = await fetch(requestUrl.toString(), {
            signal: requestController.signal,
          });
          if (!response.ok) {
            throw new Error(`Failed to load oil slick data (${response.status} ${response.statusText})`);
          }

          const fetchedData = await response.json();
          if (fetchedData?.type !== 'FeatureCollection' || !Array.isArray(fetchedData.features)) {
            throw new Error('Oil slick API returned an invalid GeoJSON FeatureCollection.');
          }
          if (mapRef.current !== map || activeSlickRequest !== requestController) return;

          const source = map.getSource('sar-oil-slicks');
          if (!(source instanceof maplibregl.GeoJSONSource)) {
            throw new Error('The sar-oil-slicks GeoJSON source is unavailable.');
          }
          const overviewSource = map.getSource('oil-slick-overview');
          if (!(overviewSource instanceof maplibregl.GeoJSONSource)) {
            throw new Error('The oil-slick-overview GeoJSON source is unavailable.');
          }
          const overviewFeatures = fetchedData.features.flatMap((feature: {
            geometry?: { coordinates?: unknown };
            properties?: Record<string, unknown>;
          }) => {
            const coordinates = spillOverviewPosition(feature.geometry?.coordinates);
            return coordinates
              ? [{
                  type: 'Feature',
                  geometry: { type: 'Point', coordinates },
                  properties: feature.properties ?? {},
                }]
              : [];
          });
          if (slickAnimationFrame !== null) {
            window.cancelAnimationFrame(slickAnimationFrame);
          }
          slickAnimationFrame = window.requestAnimationFrame(() => {
            if (mapRef.current === map && !requestController.signal.aborted) {
              source.setData(fetchedData);
              overviewSource.setData({ type: 'FeatureCollection', features: overviewFeatures });
              console.info(`[VARUNETRA] Loaded slick features: ${fetchedData.features.length}`);
            }
            slickAnimationFrame = null;
          });
        } catch (error) {
          if (requestController.signal.aborted) return;
          console.error('[VARUNETRA] Unable to load oil slick data:', error);
        } finally {
          if (activeSlickRequest === requestController) {
            activeSlickRequest = null;
            if (spillLoadingTimer !== null) {
              window.clearTimeout(spillLoadingTimer);
              spillLoadingTimer = null;
            }
            setIsFetchingSpills(false);
          }
        }
      };
      void loadSpillsOnce();
      const refreshAisVessels = async () => {
        if (!showShipsRef.current) return;
        aisRequestRef.current?.abort();
        const requestController = new AbortController();
        aisRequestRef.current = requestController;
        setIsFetchingShips(true);

        try {
          const bounds = map.getBounds();
          const requestUrl = new URL('/api/v1/vessels', window.location.origin);
          const west = bounds.getWest();
          const south = bounds.getSouth();
          const east = bounds.getEast();
          const north = bounds.getNorth();
          if (
            west >= -180 && east <= 180 && west < east &&
            south >= -90 && north <= 90 && south < north
          ) {
            requestUrl.searchParams.set('w', west.toFixed(4));
            requestUrl.searchParams.set('s', south.toFixed(4));
            requestUrl.searchParams.set('e', east.toFixed(4));
            requestUrl.searchParams.set('n', north.toFixed(4));
          }

          const response = await fetch(requestUrl.toString(), { cache: 'no-store', signal: requestController.signal });
          if (!response.ok) {
            const details = await response.text();
            throw new Error(`AIS vessels request failed (${response.status}): ${details}`);
          }
          const payload = await response.json();
          if (payload?.type !== 'FeatureCollection') throw new Error('AIS vessels API returned invalid GeoJSON');
          if (mapRef.current !== map || !showShipsRef.current || requestController.signal.aborted) return;
          const liveVessels = (Array.isArray(payload.features) ? payload.features : []).flatMap((feature: {
            geometry?: { type?: string; coordinates?: unknown };
            properties?: Record<string, unknown>;
            id?: string | number;
          }) => {
            const coordinates = feature.geometry?.coordinates;
            if (
              feature.geometry?.type !== 'Point' ||
              !Array.isArray(coordinates) ||
              typeof coordinates[0] !== 'number' ||
              typeof coordinates[1] !== 'number'
            ) return [];
            const properties = feature.properties ?? {};
            const id = String(properties.mmsi ?? feature.id ?? '');
            if (!id) return [];
            return [{
              id,
              name: String(properties.name ?? `MMSI ${id}`),
              shipType: String(properties.typeCode ?? properties.type ?? 'Unknown'),
              distanceKm: 0,
              probability: 0,
              position: [coordinates[0], coordinates[1]] as [number, number],
            }];
          });
          liveVesselsRef.current = liveVessels;
          if (selectedSlickRef.current) {
            setInspectorVessels(nearbyVessels(selectedSlickRef.current.center, liveVessels));
          }
          const source = map.getSource('ais-vessels') as maplibregl.GeoJSONSource | undefined;
          if (source) {
            if (aisFrameRef.current !== null) window.cancelAnimationFrame(aisFrameRef.current);
            aisFrameRef.current = window.requestAnimationFrame(() => {
              if (mapRef.current === map && showShipsRef.current && !requestController.signal.aborted) {
                source.setData(payload);
              }
              aisFrameRef.current = null;
            });
          }
        } catch (error) {
          if (!requestController.signal.aborted) {
            console.error('[VARUNETRA] Live AIS refresh failed:', error);
          }
        } finally {
          if (aisRequestRef.current === requestController) {
            aisRequestRef.current = null;
            setIsFetchingShips(false);
          }
        }
      };
      refreshAisVesselsRef.current = refreshAisVessels;

      handleAisViewportChange = () => {
        if (!showShipsRef.current) return;
        if (aisDebounceTimer !== null) window.clearTimeout(aisDebounceTimer);
        aisDebounceTimer = window.setTimeout(() => {
          aisDebounceTimer = null;
          void refreshAisVessels();
        }, 500);
      };
      map.on('moveend', handleAisViewportChange);
      vesselPollTimer = window.setInterval(() => {
        if (mapRef.current === map && showShipsRef.current) {
          void refreshAisVessels();
        }
      }, 10000);
      const aisLayer = createAisShipLayer('ais-ships');
      aisLayer.setShips(new Float32Array(0), 0);
      map.addLayer(aisLayer);
      aisLayerRef.current = aisLayer;

      currentsRef.current = new CurrentsOverlay(map);

      setMapInstance(map);
      setMapReady(true);
    });

    return () => {
      const requestToAbort = activeSlickRequest;
      activeSlickRequest = null;
      requestToAbort?.abort();
      if (spillLoadingTimer !== null) {
        window.clearTimeout(spillLoadingTimer);
      }
      if (slickAnimationFrame !== null) {
        window.cancelAnimationFrame(slickAnimationFrame);
      }
      if (handleAisViewportChange) {
        map.off('moveend', handleAisViewportChange);
      }
      if (aisDebounceTimer !== null) {
        window.clearTimeout(aisDebounceTimer);
      }
      if (vesselPollTimer) {
        window.clearInterval(vesselPollTimer);
      }
      selectedSlickRef.current = null;
      liveVesselsRef.current = [];
      aisRequestRef.current?.abort();
      if (aisFrameRef.current !== null) {
        window.cancelAnimationFrame(aisFrameRef.current);
      }
      refreshAisVesselsRef.current = null;
      currentsRef.current?.remove();
      currentsRef.current = null;
      aisLayerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [closeSlick, handleVesselHover, openSlick]);

  useEffect(() => {
    showShipsRef.current = showShips;
    const map = mapRef.current;
    if (!map || !mapReady) return;

    if (map.getLayer('ais-vessels-moving')) {
      map.setLayoutProperty('ais-vessels-moving', 'visibility', showShips ? 'visible' : 'none');
    }

    if (showShips) {
      void refreshAisVesselsRef.current?.();
    } else {
      aisRequestRef.current?.abort();
      if (aisFrameRef.current !== null) {
        window.cancelAnimationFrame(aisFrameRef.current);
        aisFrameRef.current = null;
      }
    }
  }, [showShips, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !map.getLayer('ais-vessels-stopped')) return;
    map.setLayoutProperty(
      'ais-vessels-stopped',
      'visibility',
      showShips && showStoppedShips ? 'visible' : 'none'
    );
  }, [showShips, showStoppedShips, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    const clusteredLayerIds = ['oil-slick-clusters', 'oil-slick-cluster-count', 'oil-slick-overview-single'];
    const rawLayerIds = ['sar-oil-slicks-fill', 'sar-oil-slicks-outline'];

    clusteredLayerIds.forEach(layerId => {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, 'visibility', isSpillClustered ? 'visible' : 'none');
      }
    });
    rawLayerIds.forEach(layerId => {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, 'visibility', isSpillClustered ? 'none' : 'visible');
      }
    });
  }, [isSpillClustered, mapReady]);

  useEffect(() => {
    selectedSlickRef.current = selectedSlick;
    if (selectedSlick) return;
    const map = mapRef.current;
    if (map?.getLayer('ais-inspector-highlight')) {
      map.setFilter('ais-inspector-highlight', ['==', ['to-string', ['get', 'mmsi']], '']);
    }
  }, [selectedSlick]);

  useEffect(() => {
    const layer = aisLayerRef.current;
    if (!layer) return;
    const packed = new Float32Array(aisShips.length * 7);
    aisShips.forEach((ship, index) => {
      packShip(packed, index, ship.lng, ship.lat, rgb(ship.color ?? '#38d6e8'), ship.course, false);
    });
    layer.setShips(packed, aisShips.length);
  }, [aisShips, mapReady]);

  useEffect(() => {
    if (!currentsEnabled || !mercatorKey) return;
    let stale = false;
    loadMercatorCurrents(mercatorKey)
      .then(({ grid, cycle, hour }) => {
        if (stale) return;
        console.info(`[VARUNETRA] Ocean currents loaded: Mercator ${cycle}/${hour}`);
        setCurrentField(grid);
        setCurrentsStatus('ready');
        const map = mapRef.current;
        if (map && map.getZoom() > 6) map.easeTo({ zoom: 4, pitch: 0, duration: 1200 });
      })
      .catch(error => {
        if (stale) return;
        console.error('[VARUNETRA] Unable to load Mercator ocean currents:', error);
        setCurrentsError(error instanceof Error ? error.message : String(error));
        setCurrentsStatus('error');
      });
    return () => {
      stale = true;
    };
  }, [currentsEnabled, mercatorKey]);

  useEffect(() => {
    currentsRef.current?.setField(currentsEnabled ? currentField : null);
  }, [currentsEnabled, currentField, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    map.setLayoutProperty('satellite-imagery-layer', 'visibility', satelliteEnabled ? 'visible' : 'none');
  }, [mapReady, satelliteEnabled]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (!projectionInitializedRef.current) {
      projectionInitializedRef.current = true;
      return;
    }
    map.setProjection({ type: view3D ? 'globe' : 'mercator' });
    map.easeTo({ pitch: view3D ? 35 : 0, duration: 500 });
  }, [mapReady, view3D]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !view3D || !globeRotationEnabled) return;

    if (map.getZoom() > 3) map.easeTo({ zoom: 2, duration: 1000 });

    let frame = 0;
    let lastTimestamp = 0;
    const rotate = (timestamp: number) => {
      if (lastTimestamp && timestamp - lastTimestamp >= 100) {
        const elapsedSeconds = Math.min((timestamp - lastTimestamp) / 1000, 0.1);
        if (!map.isMoving()) {
          const center = map.getCenter();
          map.setCenter([center.lng + elapsedSeconds * 1.5, center.lat]);
        }
        lastTimestamp = timestamp;
      } else if (!lastTimestamp) {
        lastTimestamp = timestamp;
      }
      frame = window.requestAnimationFrame(rotate);
    };

    frame = window.requestAnimationFrame(rotate);
    return () => window.cancelAnimationFrame(frame);
  }, [globeRotationEnabled, mapReady, view3D]);

  const toggleClass = (active: boolean) =>
    `flex w-full items-center gap-2 rounded px-2.5 py-2 text-left font-mono text-[10px] tracking-[0.12em] transition-colors ${
      active ? 'bg-cyan-300/10 text-cyan-100' : 'text-white/55 hover:bg-white/5 hover:text-white/85'
    }`;

  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="h-full w-full" aria-label="Marine environment map" />
      {mapReady && windEnabled && <WindBar mapRef={mapRef} controls={false} />}
      {mapReady && (
        <section aria-label="Map layers and view controls" className="absolute left-4 top-[150px] z-10 w-48 rounded border border-white/10 bg-[#080b12]/90 p-2 font-mono shadow-lg backdrop-blur-md sm:left-6">
          <p className="px-2.5 pb-2 pt-1 font-mono text-[9px] tracking-[0.18em] text-white/35">MAP CONTROLS</p>
          <button type="button" className={toggleClass(windEnabled)} aria-pressed={windEnabled} onClick={() => setWindEnabled(value => !value)}>
            <Wind size={14} /> WIND {windEnabled ? 'ON' : 'OFF'}
          </button>
          <button
            type="button"
            className={toggleClass(currentsEnabled)}
            aria-pressed={currentsEnabled}
            onClick={() => {
              setCurrentsStatus(currentField ? 'ready' : 'loading');
              setCurrentsEnabled(value => !value);
            }}
          >
            <Waves size={14} /> CURRENTS {currentsEnabled ? 'ON' : 'OFF'}
          </button>
          <button type="button" className={toggleClass(satelliteEnabled)} aria-pressed={satelliteEnabled} onClick={() => setSatelliteEnabled(value => !value)}>
            <Satellite size={14} /> SATELLITE {satelliteEnabled ? 'ON' : 'OFF'}
          </button>
          <button
            type="button"
            className={toggleClass(isSpillClustered)}
            aria-pressed={isSpillClustered}
            onClick={() => setIsSpillClustered(value => !value)}
          >
            <span className="text-orange-400" aria-hidden="true">●</span>
            SPILL CLUSTERS: {isSpillClustered ? 'ON' : 'OFF'}
          </button>
          {isFetchingSpills && (
            <p role="status" className="flex items-center gap-2 px-2.5 py-2 font-mono text-[9px] tracking-[0.12em] text-orange-200/75">
              <span className="h-3 w-3 animate-spin rounded-full border border-orange-100/30 border-t-orange-100" aria-hidden="true" />
              LOADING OIL SPILLS
            </p>
          )}
          <button type="button" className={toggleClass(showShips)} aria-pressed={showShips} onClick={() => setShowShips(value => !value)}>
            <Ship size={14} /> SHIPS {showShips ? 'ON' : 'OFF'}
            {isFetchingShips && <span className="ml-auto h-3 w-3 animate-spin rounded-full border border-cyan-100/30 border-t-cyan-100" aria-label="Loading vessels" />}
          </button>
          <button type="button" className={toggleClass(showStoppedShips)} aria-pressed={showStoppedShips} onClick={() => setShowStoppedShips(value => !value)}>
            <Anchor size={14} /> STOPPED SHIPS {showStoppedShips ? 'ON' : 'OFF'}
          </button>
          <div className="my-2 border-t border-white/10" />
          <p className="px-2.5 pb-1 font-mono text-[9px] tracking-[0.18em] text-white/35">PROJECTION</p>
          <div className="grid grid-cols-2 gap-1">
            {[false, true].map(is3D => (
              <button
                key={String(is3D)}
                type="button"
                aria-pressed={view3D === is3D}
                className={`flex items-center justify-center gap-1 rounded px-2 py-2 font-mono text-[10px] tracking-[0.12em] ${
                  view3D === is3D ? 'bg-cyan-300/10 text-cyan-100' : 'text-white/55 hover:bg-white/5 hover:text-white/85'
                }`}
                onClick={() => setView3D(is3D)}
              >
                {is3D ? <Globe2 size={13} /> : <Layers3 size={13} />}
                {is3D ? '3D' : '2D'}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`${toggleClass(globeRotationEnabled)} mt-1 disabled:cursor-not-allowed disabled:opacity-40`}
            aria-pressed={globeRotationEnabled}
            disabled={!view3D}
            onClick={() => setGlobeRotationEnabled(value => !value)}
          >
            <RotateCw size={14} /> ROTATE GLOBE {globeRotationEnabled ? 'ON' : 'OFF'}
          </button>
          {currentsEnabled && !mercatorKey && (
            <p role="status" className="px-2.5 pt-2 font-mono text-[9px] leading-4 text-sky-200/60">SET NEXT_PUBLIC_MERCATOR_API_KEY TO SHOW CURRENTS</p>
          )}
          {currentsEnabled && mercatorKey && currentsStatus === 'loading' && (
            <p role="status" className="px-2.5 pt-2 font-mono text-[9px] leading-4 text-sky-200/60">LOADING OCEAN CURRENTS…</p>
          )}
          {currentsEnabled && mercatorKey && currentsStatus === 'error' && (
            <p role="alert" className="px-2.5 pt-2 font-mono text-[9px] leading-4 text-amber-200/70">OCEAN CURRENTS UNAVAILABLE · {currentsError.slice(0, 120)}</p>
          )}
        </section>
      )}
      {mapReady && (
        <OilSlickInspector
          slick={selectedSlick}
          selectedSlickData={selectedSlickData}
          map={mapInstance}
          activeTab={activeSlickTab}
          onActiveTabChange={setActiveSlickTab}
          onClose={handleInspectorClose}
          onVesselHover={handleVesselHover}
          onVesselSelect={handleVesselSelect}
        />
      )}
    </div>
  );
}
