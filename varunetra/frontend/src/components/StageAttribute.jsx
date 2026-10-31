import { Fragment } from 'react'
import { divIcon } from 'leaflet'
import { Marker, Polyline } from 'react-leaflet'

const vesselIcons = {
  primary: divIcon({
    className: 'leaflet-vessel-icon vessel-primary',
    html: '<span>◆</span>',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  }),
  standard: divIcon({
    className: 'leaflet-vessel-icon',
    html: '<span>◆</span>',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  }),
}

function VesselLayers({ vessels }) {
  return vessels.map((vessel) => (
    <Fragment key={vessel.name}>
      <Polyline
        positions={vessel.track.map((point) => [point.lat, point.lon])}
        pathOptions={{
          color: vessel.rank === 1 ? '#2de2d0' : '#6eafb3',
          weight: vessel.rank === 1 ? 2 : 1,
          opacity: vessel.rank === 1 ? 0.95 : 0.6,
          dashArray: vessel.rank === 1 ? undefined : '5 7',
        }}
      />
      <Marker
        position={[vessel.position.lat, vessel.position.lon]}
        icon={vessel.rank === 1 ? vesselIcons.primary : vesselIcons.standard}
      />
    </Fragment>
  ))
}

function VesselLeaderboard({ vessels }) {
  return (
    <aside className="leaderboard-panel" aria-labelledby="leaderboard-title">
      <div className="leaderboard-heading">
        <div>
          <span className="stage-index">VESSEL SIGNALS</span>
          <h3 id="leaderboard-title">Ranked by Source Probability</h3>
        </div>
        <span className="ais-badge">AIS</span>
      </div>
      <div className="vessel-list">
        {vessels.slice().sort((first, second) => first.rank - second.rank).map((vessel) => (
          <div className={`vessel-row ${vessel.rank === 1 ? 'vessel-row-primary' : ''}`} key={vessel.name}>
            <span className="vessel-rank">0{vessel.rank}</span>
            <div className="vessel-details">
              <strong>{vessel.name}</strong>
              <span>{vessel.type.toUpperCase()} / {vessel.position.lat.toFixed(2)}°N {vessel.position.lon.toFixed(2)}°E</span>
              <span className="confidence-track"><i style={{ width: `${vessel.confidence * 100}%` }} /></span>
            </div>
            <span className="vessel-confidence">{Math.round(vessel.confidence * 100)}%</span>
          </div>
        ))}
      </div>
      <div className="leaderboard-footer"><span className="status-dot" /> TOP CANDIDATE HIGHLIGHTED</div>
    </aside>
  )
}

export { VesselLayers, VesselLeaderboard }

function StageAttribute({ vessels }) {
  return <VesselLayers vessels={vessels} />
}

export default StageAttribute
