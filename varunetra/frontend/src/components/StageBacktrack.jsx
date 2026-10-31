import { useEffect, useState } from 'react'
import { Circle, CircleMarker, MapContainer, Marker, Polyline, TileLayer } from 'react-leaflet'
import { divIcon } from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { VesselLayers, VesselLeaderboard } from './StageAttribute'

const detectionIcon = divIcon({
  className: 'leaflet-detection-icon',
  html: '<span></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
})

const particleIcon = divIcon({
  className: 'leaflet-particle-icon',
  html: '<span></span>',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
})

function interpolatePosition(path, elapsed, duration) {
  if (path.length < 2) return [path[0].lat, path[0].lon]
  const progress = Math.min(elapsed / duration, 1)
  const targetTime = path[path.length - 1].t * progress
  const segmentIndex = path.findIndex((point, index) => point.t >= targetTime && index > 0)
  const nextIndex = segmentIndex === -1 ? path.length - 1 : segmentIndex
  const previous = path[nextIndex - 1]
  const next = path[nextIndex]
  const segmentProgress = (targetTime - previous.t) / (next.t - previous.t || 1)
  return [
    previous.lat + (next.lat - previous.lat) * segmentProgress,
    previous.lon + (next.lon - previous.lon) * segmentProgress,
  ]
}

function StageBacktrack({ scenario, showAttribute }) {
  const [particlePosition, setParticlePosition] = useState([
    scenario.backtrack.particle_path[0].lat,
    scenario.backtrack.particle_path[0].lon,
  ])
  const [isComplete, setIsComplete] = useState(false)
  const path = scenario.backtrack.particle_path
  const pathCoordinates = path.map((point) => [point.lat, point.lon])

  useEffect(() => {
    const duration = 3600
    const startedAt = performance.now()
    let frameId
    const animate = (now) => {
      const elapsed = now - startedAt
      setParticlePosition(interpolatePosition(path, elapsed, duration))
      if (elapsed < duration) {
        frameId = requestAnimationFrame(animate)
      } else {
        setIsComplete(true)
      }
    }
    frameId = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frameId)
  }, [path])

  const detectionPosition = [scenario.location.lat, scenario.location.lon]
  const originPosition = [scenario.backtrack.origin_center.lat, scenario.backtrack.origin_center.lon]

  return (
    <section className="stage-card backtrack-card" aria-labelledby="backtrack-title">
      <div className="stage-heading">
        <div>
          <span className="stage-index">04 / BACKTRACK</span>
          <h2 id="backtrack-title">Backtracking Trajectory — Origin Probability</h2>
        </div>
        <span className={`trajectory-status ${isComplete ? 'complete' : ''}`}>
          {isComplete ? 'ORIGIN LOCKED' : 'RECONSTRUCTING'}
        </span>
      </div>
      <div className="map-frame">
        <MapContainer center={detectionPosition} zoom={7} scrollWheelZoom className="backtrack-map">
          <TileLayer
            attribution="&copy; OpenStreetMap &copy; CARTO"
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          <Marker position={detectionPosition} icon={detectionIcon} />
          <Polyline positions={pathCoordinates} pathOptions={{ color: '#6eafb3', weight: 1, dashArray: '4 8', opacity: 0.65 }} />
          <Marker position={particlePosition} icon={particleIcon} />
          {isComplete && scenario.backtrack.heatmap_points.map((point) => (
            <Circle
              key={`${point.lat}-${point.lon}`}
              center={[point.lat, point.lon]}
              radius={Math.max(point.intensity * 13000, 5000)}
              pathOptions={{ color: '#2de2d0', weight: 1, opacity: 0.55, fillColor: '#00bcd4', fillOpacity: point.intensity * 0.2 }}
            />
          ))}
          {isComplete && <CircleMarker center={originPosition} radius={5} pathOptions={{ color: '#eefafa', fillColor: '#2de2d0', fillOpacity: 1, weight: 2 }} />}
          {showAttribute && <VesselLayers vessels={scenario.ais_vessels} />}
        </MapContainer>
        <div className="map-readout map-readout-top"><span className="map-live-dot" /> LIVE TRAJECTORY REPLAY</div>
        <div className="map-readout map-readout-bottom">{isComplete ? 'PROBABLE ORIGIN ZONE' : 'WIND-CURRENT MODEL / T−43H'}</div>
        <div className="map-legend"><span><i className="legend-dot detection-dot" /> DETECTION</span><span><i className="legend-dot particle-dot" /> PARTICLE PATH</span><span><i className="legend-dot origin-dot" /> ORIGIN PROBABILITY</span></div>
      </div>
      <div className="stage-caption backtrack-caption">
        <span className="signal-bars" aria-hidden="true"><i /><i /><i /><i /></span>
        <span>{isComplete ? 'Particles converged on the highest-probability source region' : 'Tracing fluid motion backward from detection point'}</span>
        <span className="caption-value">{scenario.backtrack.origin_center.lat.toFixed(2)}°N / {scenario.backtrack.origin_center.lon.toFixed(2)}°E</span>
      </div>
      {showAttribute && <VesselLeaderboard vessels={scenario.ais_vessels} />}
    </section>
  )
}

export default StageBacktrack
