const express = require('express');
const WebSocket = require('ws');

const router = express.Router();
const AISSTREAM_URL = 'wss://stream.aisstream.io/v0/stream';
const AISSTREAM_RECONNECT_MS = 5_000;
const AISSTREAM_TTL_MS = 30 * 60 * 1_000;
const vesselsByMmsi = new Map();

let aisSocket = null;
let aisReconnectTimer = null;
let streamEnabled = false;

function vesselTypeMeta(rawType) {
  const normalized = String(rawType ?? '').trim().toLowerCase();
  const numericType = Number(normalized);
  if (Number.isInteger(numericType) && normalized !== '') {
    if (numericType === 30) return { code: 'fishing', label: 'Fishing', color: '#f59e0b' };
    if (numericType >= 60 && numericType < 70) return { code: 'passenger', label: 'Passenger', color: '#60a5fa' };
    if (numericType >= 70 && numericType < 80) return { code: 'cargo', label: 'Cargo', color: '#34d399' };
    if (numericType >= 80 && numericType < 90) return { code: 'tanker', label: 'Tanker', color: '#7f1d1d' };
  }
  if (!normalized) return { code: 'unknown', label: 'Unknown', color: '#a9b3c2' };
  if (/cargo|container|bulk|ro-ro|vehicle|general cargo/.test(normalized)) return { code: 'cargo', label: 'Cargo', color: '#34d399' };
  if (/tanker|oil|chemical|gas/.test(normalized)) return { code: 'tanker', label: 'Tanker', color: '#7f1d1d' };
  if (/passenger|cruise|ferry/.test(normalized)) return { code: 'passenger', label: 'Passenger', color: '#60a5fa' };
  if (/fishing|trawler/.test(normalized)) return { code: 'fishing', label: 'Fishing', color: '#f59e0b' };
  if (/tug|pilot|tow/.test(normalized)) return { code: 'tug', label: 'Tug', color: '#fbbf24' };
  if (/military|navy|government/.test(normalized)) return { code: 'military', label: 'Military', color: '#c084fc' };
  if (/leisure|pleasure|yacht/.test(normalized)) return { code: 'pleasure', label: 'Pleasure', color: '#6ee7b7' };
  if (/search|rescue|sar/.test(normalized)) return { code: 'search_and_rescue', label: 'SAR', color: '#fb7185' };
  return { code: 'unknown', label: 'Unknown', color: '#a9b3c2' };
}

function readNestedValue(object, path) {
  let cursor = object;
  for (const key of path) {
    if (cursor == null || !(key in cursor)) return undefined;
    cursor = cursor[key];
  }
  return cursor;
}

function purgeStaleVessels() {
  const cutoff = Date.now() - AISSTREAM_TTL_MS;
  for (const [mmsi, vessel] of vesselsByMmsi.entries()) {
    if ((vessel.updatedAt ?? 0) < cutoff) {
      vesselsByMmsi.delete(mmsi);
    }
  }
}

function serializeVessel(vessel) {
  if (!vessel || vessel.lat == null || vessel.lon == null) return null;
  const meta = vesselTypeMeta(vessel.type);
  return {
    mmsi: vessel.mmsi,
    name: vessel.name || `MMSI ${vessel.mmsi}`,
    imo: vessel.imo || null,
    type: vessel.type || 'Unknown',
    typeCode: meta.code,
    typeLabel: meta.label,
    color: meta.color,
    destination: vessel.destination || null,
    lat: Number(vessel.lat),
    lon: Number(vessel.lon),
    sog: vessel.sog ?? null,
    cog: vessel.cog ?? null,
    trueHeading: vessel.trueHeading ?? null,
    updatedAt: vessel.updatedAt ?? Date.now(),
  };
}

function buildVesselGeoJson(bounds) {
  const features = [];
  for (const vessel of vesselsByMmsi.values()) {
    const record = serializeVessel(vessel);
    if (!record) continue;
    if (
      bounds &&
      (record.lon < bounds.w || record.lon > bounds.e ||
        record.lat < bounds.s || record.lat > bounds.n)
    ) continue;
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [record.lon, record.lat] },
      properties: {
        mmsi: record.mmsi,
        name: record.name,
        imo: record.imo,
        destination: record.destination,
        type: record.type,
        typeCode: record.typeCode,
        typeLabel: record.typeLabel,
        color: record.color,
        sog: record.sog,
        cog: record.cog,
        trueHeading: record.trueHeading,
        heading: record.trueHeading ?? record.cog ?? null,
        updatedAt: record.updatedAt,
      },
    });
    if (features.length >= 3_000) break;
  }
  return { type: 'FeatureCollection', features };
}

function mergeAisMessage(record, message) {
  if (!record) return null;
  const body = message.Message || message;
  const position = body.PositionReport || body.Position || body;
  const staticData = body.ShipStaticData || body.StaticData || body;

  if (position && typeof position === 'object') {
    const lat = Number(position.Latitude ?? position.lat);
    const lon = Number(position.Longitude ?? position.lon);
    const sog = Number(position.SpeedOverGround ?? position.Sog ?? position.sog ?? record.sog);
    const cog = Number(position.CourseOverGround ?? position.Cog ?? position.cog ?? record.cog);
    const trueHeading = Number(position.TrueHeading ?? position.trueHeading ?? record.trueHeading);
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      record.lat = lat;
      record.lon = lon;
    }
    if (Number.isFinite(sog)) record.sog = sog;
    if (Number.isFinite(cog)) record.cog = cog;
    if (Number.isFinite(trueHeading)) record.trueHeading = trueHeading;
  }

  if (staticData && typeof staticData === 'object') {
    const kind = readNestedValue(staticData, ['ShipType']) ?? readNestedValue(staticData, ['Type']) ?? readNestedValue(staticData, ['type']);
    const name = readNestedValue(staticData, ['ShipName']) ?? readNestedValue(staticData, ['Name']) ?? readNestedValue(staticData, ['name']) ?? record.name;
    const destination = readNestedValue(staticData, ['Destination']) ?? readNestedValue(staticData, ['destination']) ?? record.destination;
    const imo = readNestedValue(staticData, ['IMO']) ?? readNestedValue(staticData, ['ImoNumber']) ?? readNestedValue(staticData, ['imo']) ?? record.imo;
    if (name) record.name = String(name);
    if (destination) record.destination = String(destination);
    if (imo) record.imo = String(imo);
    if (kind) record.type = String(kind);
  }

  record.updatedAt = Date.now();
  return record;
}

function applyAisMessage(rawMessage) {
  const message = rawMessage && typeof rawMessage === 'object' ? rawMessage : null;
  if (!message) return;

  const mmsiValue = Number(
    readNestedValue(message, ['MetaData', 'MMSI']) ??
      readNestedValue(message, ['MMSI']) ??
      readNestedValue(message, ['mmsi']) ??
      readNestedValue(message, ['meta', 'MMSI'])
  );
  const mmsi = Number.isFinite(mmsiValue) && mmsiValue > 0
    ? String(Math.trunc(mmsiValue))
    : null;
  if (!mmsi) return;

  const existing = vesselsByMmsi.get(mmsi) || {
    mmsi,
    lat: null,
    lon: null,
    sog: null,
    cog: null,
    trueHeading: null,
    updatedAt: Date.now(),
  };
  const merged = mergeAisMessage(existing, message);
  if (merged && merged.lat != null && merged.lon != null) {
    vesselsByMmsi.set(mmsi, merged);
  }
}

function scheduleAisReconnect() {
  if (!streamEnabled || aisReconnectTimer) return;
  aisReconnectTimer = setTimeout(() => {
    aisReconnectTimer = null;
    connectAisStream();
  }, AISSTREAM_RECONNECT_MS);
  aisReconnectTimer.unref();
}

function connectAisStream() {
  const apiKey = process.env.AISSTREAM_KEY;
  if (!apiKey) {
    console.warn('[AIS] AISSTREAM_KEY missing; live AIS disabled');
    return;
  }

  if (aisSocket && (aisSocket.readyState === WebSocket.OPEN || aisSocket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  console.log('[AIS] Connecting to AISStream.io...');
  const socket = new WebSocket(AISSTREAM_URL);
  aisSocket = socket;

  socket.on('open', () => {
    socket.send(JSON.stringify({
      APIKey: apiKey,
      BoundingBoxes: [[[-90, -180], [90, 180]]],
      FilterMessageTypes: ['PositionReport', 'ShipStaticData'],
    }), error => {
      if (error) console.error('[AIS] Failed to subscribe to AISStream:', error);
    });
  });

  socket.on('message', data => {
    try {
      const payload = JSON.parse(data.toString());
      const messages = Array.isArray(payload) ? payload : [payload];
      for (const message of messages) {
        applyAisMessage(message);
      }
      purgeStaleVessels();
    } catch (error) {
      console.error('[AIS] Unable to decode AISStream message:', error);
    }
  });

  socket.on('error', error => {
    console.error('[AIS] AISStream connection error:', error.message || error);
  });

  socket.on('close', () => {
    if (aisSocket === socket) aisSocket = null;
    if (!streamEnabled) return;
    console.warn(`[AIS] AISStream disconnected; retrying in ${AISSTREAM_RECONNECT_MS / 1_000}s`);
    scheduleAisReconnect();
  });
}

function startAisStream() {
  if (streamEnabled) return;
  streamEnabled = true;
  connectAisStream();
  setInterval(purgeStaleVessels, 60 * 1_000).unref();
}

function stopAisStream() {
  streamEnabled = false;
  if (aisReconnectTimer) {
    clearTimeout(aisReconnectTimer);
    aisReconnectTimer = null;
  }
  if (aisSocket) {
    const socket = aisSocket;
    aisSocket = null;
    socket.close();
  }
}

function parseBounds(query) {
  const keys = ['w', 's', 'e', 'n'];
  const supplied = keys.filter(key => query[key] !== undefined);
  if (supplied.length === 0) return null;
  if (supplied.length !== keys.length) return undefined;

  const values = keys.map(key => Number(query[key]));
  if (!values.every(Number.isFinite)) return undefined;
  const [w, s, e, n] = values;
  if (
    w < -180 || w > 180 || e < -180 || e > 180 || w >= e ||
    s < -90 || s > 90 || n < -90 || n > 90 || s >= n
  ) {
    return undefined;
  }
  return { w, s, e, n };
}

router.get('/', (req, res) => {
  const bounds = parseBounds(req.query);
  if (bounds === undefined) {
    return res.status(400).json({ error: 'Valid bounding box required: w,s,e,n' });
  }
  return res.json(buildVesselGeoJson(bounds));
});

router.get('/:mmsi', (req, res) => {
  const record = serializeVessel(vesselsByMmsi.get(String(req.params.mmsi)));
  if (!record) {
    return res.status(404).json({ error: 'Vessel not found' });
  }

  return res.json({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [record.lon, record.lat] },
    properties: {
      mmsi: record.mmsi,
      name: record.name,
      imo: record.imo,
      destination: record.destination,
      type: record.type,
      typeCode: record.typeCode,
      typeLabel: record.typeLabel,
      color: record.color,
      sog: record.sog,
      cog: record.cog,
      trueHeading: record.trueHeading,
      updatedAt: record.updatedAt,
    },
  });
});

module.exports = {
  router,
  startAisStream,
  stopAisStream,
  vesselsByMmsi,
  applyAisMessage,
  buildVesselGeoJson,
};
