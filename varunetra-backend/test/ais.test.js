const assert = require('node:assert/strict');
const { after, before, test } = require('node:test');
const express = require('express');
const {
  router,
  applyAisMessage,
  buildVesselGeoJson,
  vesselsByMmsi,
} = require('../src/routes/ais');

const app = express();
let server;
let baseUrl;

before(async () => {
  app.use('/api/v1/vessels', router);
  server = app.listen(0);
  await new Promise(resolve => server.once('listening', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
  });
  vesselsByMmsi.clear();
});

test('merges AISStream position and static messages into viewport-filtered GeoJSON', async () => {
  vesselsByMmsi.clear();
  applyAisMessage({
    MessageType: 'PositionReport',
    MetaData: { MMSI: 123456789 },
    Message: {
      PositionReport: {
        Latitude: 37.5,
        Longitude: -122.5,
        Sog: 8.2,
        Cog: 91,
        TrueHeading: 90,
      },
    },
  });
  applyAisMessage({
    MessageType: 'ShipStaticData',
    MetaData: { MMSI: 123456789 },
    Message: {
      ShipStaticData: {
        Name: 'Test Vessel',
        ShipType: 70,
        ImoNumber: 9876543,
        Destination: 'OAKLAND',
      },
    },
  });

  const result = buildVesselGeoJson({ w: -123, s: 37, e: -122, n: 38 });
  assert.equal(result.features.length, 1);
  assert.deepEqual(result.features[0], {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [-122.5, 37.5] },
    properties: {
      mmsi: '123456789',
      name: 'Test Vessel',
      imo: '9876543',
      destination: 'OAKLAND',
      type: '70',
      typeCode: 'cargo',
      typeLabel: 'Cargo',
      color: '#34d399',
      sog: 8.2,
      cog: 91,
      trueHeading: 90,
      heading: 90,
      updatedAt: vesselsByMmsi.get('123456789').updatedAt,
    },
  });
});

test('serves collection, unfiltered collection, and single-vessel endpoints', async () => {
  const allVessels = await fetch(`${baseUrl}/api/v1/vessels`);
  assert.equal(allVessels.status, 200);
  assert.equal((await allVessels.json()).type, 'FeatureCollection');

  const vessel = await fetch(`${baseUrl}/api/v1/vessels/123456789`);
  assert.equal(vessel.status, 200);
  const feature = await vessel.json();
  assert.equal(feature.type, 'Feature');
  assert.deepEqual(feature.geometry.coordinates, [-122.5, 37.5]);
  assert.equal(feature.properties.typeCode, 'cargo');

  const missing = await fetch(`${baseUrl}/api/v1/vessels/000000000`);
  assert.equal(missing.status, 404);
});

test('rejects incomplete or invalid viewport bounds', async () => {
  const incomplete = await fetch(`${baseUrl}/api/v1/vessels?w=-123&s=37`);
  assert.equal(incomplete.status, 400);

  const invalid = await fetch(`${baseUrl}/api/v1/vessels?w=-200&s=37&e=-122&n=38`);
  assert.equal(invalid.status, 400);
});
