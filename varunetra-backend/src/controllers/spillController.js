const prisma = require('../db/prismaClient');
const SPILL_CACHE_REFRESH_MS = 24 * 60 * 60 * 1000;

let cachedSpillsGeoJSON = null;
let refreshInProgress = null;
let dbCallCount = 0;

/**
 * Validates that a position is a [lon, lat] or [lon, lat, elevation] coordinate.
 */
function isPosition(position) {
  return (
    Array.isArray(position) &&
    (position.length === 2 || position.length === 3) &&
    position.every(Number.isFinite) &&
    position[0] >= -180 &&
    position[0] <= 180 &&
    position[1] >= -90 &&
    position[1] <= 90
  );
}

/**
 * Validates that a ring is a closed LinearRing (first and last positions must match).
 */
function isLinearRing(ring) {
  if (!Array.isArray(ring) || ring.length < 4 || !ring.every(isPosition)) {
    return false;
  }

  const first = ring[0];
  const last = ring[ring.length - 1];
  return first.length === last.length && first.every((value, index) => value === last[index]);
}

/**
 * Validates that coordinates form a valid Polygon or MultiPolygon.
 */
function isPolygonCoordinates(coordinates) {
  return (
    Array.isArray(coordinates) &&
    coordinates.length > 0 &&
    coordinates.every(isLinearRing)
  );
}

/**
 * Safely parses polygon GeoJSON string and validates geometry.
 * Returns the parsed geometry or null if invalid (logs to server console).
 * This function does NOT throw; it gracefully returns null for invalid data.
 */
function parseGeometry(polygonGeojson, spillId) {
  if (!polygonGeojson || typeof polygonGeojson !== 'string') {
    console.warn(`[Spill ${spillId}] polygon_geojson is null, empty, or not a string`);
    return null;
  }

  let geometry;
  try {
    geometry = JSON.parse(polygonGeojson);
  } catch (error) {
    console.warn(
      `[Spill ${spillId}] Failed to parse polygon_geojson: ${error.message}`
    );
    return null;
  }

  const validPolygon =
    geometry?.type === 'Polygon' && isPolygonCoordinates(geometry.coordinates);

  const validMultiPolygon =
    geometry?.type === 'MultiPolygon' &&
    Array.isArray(geometry.coordinates) &&
    geometry.coordinates.length > 0 &&
    geometry.coordinates.every(isPolygonCoordinates);

  if (!validPolygon && !validMultiPolygon) {
    console.warn(
      `[Spill ${spillId}] Invalid polygon geometry. Type: ${geometry?.type ?? 'unknown'}`
    );
    return null;
  }

  return geometry;
}

async function refreshSpillsCache() {
  if (refreshInProgress) return refreshInProgress;

  const startTime = Date.now();
  refreshInProgress = (async () => {
    dbCallCount++;
    const spills = await prisma.spill.findMany({
      orderBy: { imageTimeUtc: 'desc' },
      select: {
        spillId: true,
        kind: true,
        imageTimeUtc: true,
        sceneName: true,
        polygonGeojson: true,
        centroidLat: true,
        centroidLon: true,
        areaKm2: true,
        detectionConfidence: true,
        modelVersion: true,
        ageHours: true,
        ageSigmaHours: true,
        originLat: true,
        originLon: true,
        originRadiusKm: true,
        originSource: true,
        originHeatmapPath: true,
        windowStartUtc: true,
        windowEndUtc: true,
        windowSource: true,
        dataSource: true,
        notes: true,
        createdUtc: true,
      },
    });

    const features = [];
    let skippedCount = 0;

    for (const spill of spills) {
      const geometry = parseGeometry(spill.polygonGeojson, spill.spillId);
      if (!geometry) {
        skippedCount++;
        continue;
      }

      features.push({
        type: 'Feature',
        id: spill.spillId,
        geometry,
        properties: {
          id: spill.spillId,
          spill_id: spill.spillId,
          kind: spill.kind,
          timestamp: spill.imageTimeUtc,
          image_time_utc: spill.imageTimeUtc,
          scene_name: spill.sceneName,
          centroid_lat: spill.centroidLat,
          centroid_lon: spill.centroidLon,
          area: spill.areaKm2,
          area_km2: spill.areaKm2,
          confidence: spill.detectionConfidence,
          detection_confidence: spill.detectionConfidence,
          model_version: spill.modelVersion,
          age_estimate: spill.ageHours,
          age_hours: spill.ageHours,
          est_age_hr: spill.ageHours,
          age_sigma_hours: spill.ageSigmaHours,
          origin_lat: spill.originLat,
          origin_lon: spill.originLon,
          origin_radius_km: spill.originRadiusKm,
          origin_source: spill.originSource,
          origin_heatmap_path: spill.originHeatmapPath,
          window_start_utc: spill.windowStartUtc,
          window_end_utc: spill.windowEndUtc,
          window_source: spill.windowSource,
          data_source: spill.dataSource,
          notes: spill.notes,
          created_utc: spill.createdUtc,
        },
      });
    }

    if (skippedCount > 0) {
      console.warn(`[API] Skipped ${skippedCount} features due to invalid geometry`);
    }

    cachedSpillsGeoJSON = { type: 'FeatureCollection', features };
    console.info(
      `[API] Spill cache refreshed: ${features.length} features (${Date.now() - startTime}ms)`
    );
    console.log('[DB] Spill cache refreshed. Total DB calls since boot:', dbCallCount);
    return cachedSpillsGeoJSON;
  })();

  try {
    return await refreshInProgress;
  } finally {
    refreshInProgress = null;
  }
}

async function getSlicks(req, res) {
  if (!cachedSpillsGeoJSON) {
    return res.status(503).json({ error: 'Spill data cache is not ready' });
  }
  return res.json(cachedSpillsGeoJSON);
}

async function getReportedCulprits(req, res) {
  const spillId = req.params.spill_id;
  const culprits = await prisma.reportedCulprit.findMany({
    where: { spillId },
    orderBy: { id: 'asc' },
    select: {
      id: true,
      spillId: true,
      mmsi: true,
      vesselName: true,
      imo: true,
      sourceTitle: true,
      sourceUrl: true,
      reportDate: true,
      notes: true,
    },
  });

  return res.json(culprits.map(culprit => ({
    id: culprit.id,
    spill_id: culprit.spillId,
    mmsi: culprit.mmsi,
    vessel_name: culprit.vesselName,
    imo: culprit.imo,
    source_title: culprit.sourceTitle,
    source_url: culprit.sourceUrl,
    report_date: culprit.reportDate,
    notes: culprit.notes,
  })));
}

function startSpillCacheRefresh() {
  void refreshSpillsCache().catch(error => {
    console.error('[API] Initial spill cache refresh failed:', error);
  });

  const timer = setInterval(() => {
    void refreshSpillsCache().catch(error => {
      console.error('[API] Spill cache refresh failed:', error);
    });
  }, SPILL_CACHE_REFRESH_MS);
  timer.unref();
}

module.exports = {
  getSlicks,
  getReportedCulprits,
  refreshSpillsCache,
  startSpillCacheRefresh,
  SPILL_CACHE_REFRESH_MS,
};