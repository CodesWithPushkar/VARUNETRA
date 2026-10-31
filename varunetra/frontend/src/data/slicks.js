const shippingLanes = [
  {
    name: 'North Sea',
    eezOptions: ['United Kingdom EEZ', 'Norway EEZ', 'Netherlands EEZ', 'International waters'],
    centers: [[56.6, 2.8], [57.2, 3.5], [58.1, 1.8], [56.0, 0.4], [55.1, 4.2], [54.0, 4.1], [53.2, 3.3], [57.7, 4.6], [58.6, 5.2], [55.8, -1.2]],
  },
  {
    name: 'Western Mediterranean',
    eezOptions: ['Spain EEZ', 'France EEZ', 'Algeria EEZ', 'Tunisia EEZ', 'International waters'],
    centers: [[38.4, 6.5], [37.2, 8.3], [36.3, 12.8], [35.4, 16.4], [36.8, 5.0], [38.0, 9.7], [35.0, 10.5], [36.2, 3.1]],
  },
  {
    name: 'Eastern Mediterranean',
    eezOptions: ['Greece EEZ', 'Cyprus EEZ', 'Egypt EEZ', 'Turkey EEZ', 'International waters'],
    centers: [[34.6, 21.2], [33.8, 26.7], [35.7, 31.8], [36.0, 18.5], [37.5, 15.5], [32.5, 29.4], [34.2, 33.2], [36.3, 28.0]],
  },
  {
    name: 'Persian Gulf',
    eezOptions: ['Iran EEZ', 'Oman EEZ', 'United Arab Emirates EEZ', 'Qatar EEZ', 'International waters'],
    centers: [[26.1, 53.0], [26.9, 52.0], [27.3, 51.2], [25.5, 54.0], [24.9, 55.1], [26.7, 50.4], [25.8, 49.8], [24.7, 53.4]],
  },
  {
    name: 'South China Sea',
    eezOptions: ['Vietnam EEZ', 'Philippines EEZ', 'Malaysia EEZ', 'Brunei EEZ', 'International waters'],
    centers: [[14.2, 114.8], [13.1, 116.2], [11.5, 114.1], [17.3, 115.5], [15.1, 112.4], [9.2, 111.8], [12.0, 118.0], [18.1, 111.2], [10.4, 117.0], [16.2, 118.4]],
  },
  {
    name: 'Malacca Strait',
    eezOptions: ['Indonesia EEZ', 'Malaysia EEZ', 'Singapore Strait', 'International waters'],
    centers: [[4.3, 99.5], [3.8, 100.8], [2.8, 101.4], [1.9, 102.3], [5.4, 98.8], [6.5, 98.5]],
  },
  {
    name: 'Red Sea and Gulf of Aden',
    eezOptions: ['Saudi Arabia EEZ', 'Egypt EEZ', 'Yemen EEZ', 'International waters'],
    centers: [[12.5, 46.5], [13.1, 49.2], [14.0, 50.5], [16.3, 40.5], [19.2, 38.6], [21.7, 37.2]],
  },
  {
    name: 'Indian Ocean and Arabian Sea',
    eezOptions: ['India EEZ', 'Oman EEZ', 'Somalia EEZ', 'International waters'],
    centers: [[19.05, 68.9], [19.24, 69.17], [18.82, 68.61], [17.7, 66.3], [20.8, 64.2], [15.2, 67.8], [13.4, 64.5], [21.8, 66.6], [22.4, 62.8], [16.6, 62.1], [14.5, 69.5], [20.0, 70.4], [12.8, 61.5], [18.1, 63.7], [21.5, 66.5], [15.8, 64.8], [17.1, 70.4], [13.8, 66.1], [20.5, 61.2], [16.9, 68.4]],
  },
  {
    name: 'Atlantic and Caribbean routes',
    eezOptions: ['Panama EEZ', 'Colombia EEZ', 'Portugal EEZ', 'International waters'],
    centers: [[9.5, -79.5], [12.0, -75.0], [18.0, -68.0], [25.0, -60.0], [33.0, -50.0], [41.0, -35.0]],
  },
]

const globalSlickSeeds = shippingLanes.flatMap((lane) => lane.centers.map((center) => ({
  center,
  lane: lane.name,
  eezOptions: lane.eezOptions,
})))

const regionColors = ['#38bdf8', '#2dd4bf', '#a3a3a3', '#f59e0b']
const slickColors = ['#facc15', '#fbbf24', '#eab308', '#f59e0b']
const vesselNames = ['MT SEA GULL', 'BW TULIP', 'DESERT ROSE', 'MV OCEAN CROWN', 'MT BLUE HORIZON', 'PACIFIC MERIDIAN', 'GULF TRADER', 'MV CORAL BAY']
const vesselTypes = ['Crude Oil Tanker', 'Bulk Carrier']
const vesselFlags = ['Panama', 'Liberia', 'Marshall Islands', 'Singapore', 'Malta', 'Bahamas']

function createRandom(seed) {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 4294967296
  }
}

function createRegionPath(random) {
  const pointCount = 6 + Math.floor(random() * 4)
  const rotation = random() * Math.PI * 2
  const points = Array.from({ length: pointCount }, (_, index) => {
    const angle = rotation + (index / pointCount) * Math.PI * 2
    const radius = 0.62 + random() * 0.42
    const x = 50 + Math.cos(angle) * 38 * radius
    const y = 35 + Math.sin(angle) * 27 * radius
    return [x.toFixed(1), y.toFixed(1)]
  })
  return `M${points.map(([x, y]) => `${x} ${y}`).join(' L')} Z`
}

function createGeometryRegions(random, area) {
  const regionCount = 1 + Math.floor(random() * 4)
  const weights = Array.from({ length: regionCount }, () => 0.4 + random())
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0)

  return weights.map((weight, index) => ({
    id: `region-${index + 1}`,
    name: `Region ${index + 1}`,
    area_km2: Number((area * weight / totalWeight).toFixed(1)),
    color: regionColors[index % regionColors.length],
    path: createRegionPath(random),
  }))
}

function createPotentialSources(random, target) {
  const vesselCount = 2 + Math.floor(random() * 2)
  const availableNames = [...vesselNames]

  return Array.from({ length: vesselCount }, () => {
    const nameIndex = Math.floor(random() * availableNames.length)
    const [name] = availableNames.splice(nameIndex, 1)
    const direction = random() * Math.PI * 2
    const distance = 2.5 + random() * 3.5
    const latitudeScale = Math.cos(target.lat * Math.PI / 180)
    const start = {
      lat: target.lat + Math.cos(direction) * distance,
      lon: target.lon + Math.sin(direction) * distance / latitudeScale,
    }
    const bend = (random() - 0.5) * 1.2
    const midpoint = {
      lat: (start.lat + target.lat) / 2 - Math.sin(direction) * bend,
      lon: (start.lon + target.lon) / 2 + Math.cos(direction) * bend / latitudeScale,
    }

    return {
      name,
      type: vesselTypes[Math.floor(random() * vesselTypes.length)],
      matchScore: Math.floor(60 + random() * 39),
      mmsi: String(100000000 + Math.floor(random() * 900000000)),
      flag: vesselFlags[Math.floor(random() * vesselFlags.length)],
      track: [
        { ...start, t: 0 },
        { ...midpoint, t: 1 },
        { lat: target.lat, lon: target.lon, t: 2 },
      ],
    }
  })
}

function createSlickFeature(seed, index) {
  const random = createRandom(20260926 + index * 7919)
  const { center, lane, eezOptions } = seed
  const [baseLatitude, baseLongitude] = center
  const latitude = baseLatitude + (random() - 0.5) * 0.12
  const longitude = baseLongitude + (random() - 0.5) * 0.12
  const area = Number((1.8 + random() * 16.2).toFixed(1))
  const length = Number((2.4 + random() * 13.6).toFixed(1))
  const detectionOffsetHours = Math.floor(random() * 168)
  const detectionDate = new Date(Date.UTC(2026, 7, 14, 4, 22) - detectionOffsetHours * 60 * 60 * 1000)
  const timestamp = detectionDate.toISOString().replace(/[-:]/g, '').replace('.000', '')
  const id = `slick-${String(index + 1).padStart(3, '0')}`
  const ageHours = Number((18 + random() * 62).toFixed(1))
  const emulsificationLevels = ['Low', 'Medium', 'High']
  const longitudeRadius = 0.022 + random() * 0.018
  const latitudeRadius = 0.018 + random() * 0.014
  const rotation = random() * Math.PI * 2
  const ring = Array.from({ length: 9 }, (_, pointIndex) => {
    const angle = rotation + (pointIndex / 8) * Math.PI * 2
    const radius = 0.68 + random() * 0.42
    return [
      longitude + Math.cos(angle) * longitudeRadius * radius,
      latitude + Math.sin(angle) * latitudeRadius * radius,
    ]
  })
  ring[ring.length - 1] = ring[0]

  return {
    type: 'Feature',
    id,
    properties: {
      slick_id: id.toUpperCase(),
      detection_time: detectionDate.toISOString(),
      length_km: length,
      area_km2: area,
      centroid: { lat: latitude, lon: longitude },
      scene_id: `S1A_IW_GRDH_1SDV_${timestamp}`,
      eez: eezOptions[Math.floor(random() * eezOptions.length)],
      shipping_lane: lane,
      fragmentation: 1 + Math.floor(random() * 6),
      elongation: Number((1.1 + random() * 2.8).toFixed(1)),
      age_hours: ageHours,
      age_uncertainty_hours: Number((2.5 + random() * 6.5).toFixed(1)),
      trajectory: {
        netDriftAngle: Number((random() * 360).toFixed(1)),
        uncertaintySpread: Number((12 + random() * 30).toFixed(1)),
      },
      environment: {
        windSpeed: Number((5 + random() * 24).toFixed(1)),
        windDirection: Math.floor(random() * 360),
        currentSpeed: Number((0.1 + random() * 1.7).toFixed(2)),
        currentDirection: Math.floor(random() * 360),
      },
      weathering: {
        evaporationLoss: Number(Math.min(72, 9 + ageHours * 0.55 + random() * 9).toFixed(1)),
        emulsification: emulsificationLevels[Math.floor(random() * emulsificationLevels.length)],
      },
      geometryRegions: createGeometryRegions(random, area),
      potentialSources: createPotentialSources(random, { lat: latitude, lon: longitude }),
      overviewPath: createRegionPath(random),
      color: slickColors[index % slickColors.length],
    },
    geometry: {
      type: 'Polygon',
      coordinates: [ring],
    },
  }
}

const slicks = {
  type: 'FeatureCollection',
  features: globalSlickSeeds.map(createSlickFeature),
}

export default slicks