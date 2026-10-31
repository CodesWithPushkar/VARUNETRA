import { useEffect, useState } from 'react'
import { divIcon } from 'leaflet'
import { GeoJSON, MapContainer, Marker, Polyline, ScaleControl, TileLayer, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import slicks from './data/slicks'
import SlickAnalysisPanels from './components/SlickAnalysisPanels'

const globalSlickPositions = slicks.features.map((feature) => [
  feature.properties.centroid.lat,
  feature.properties.centroid.lon,
])

function FitGlobalExtent({ positions }) {
  const map = useMap()

  useEffect(() => {
    map.fitBounds(positions, { padding: [28, 28], maxZoom: 2 })
  }, [map, positions])

  return null
}

function createSlickIcon(feature, isSelected, zoom) {
  const fillColor = isSelected ? '#0f766e' : '#8a5c11'
  const strokeColor = isSelected ? '#5eead4' : '#facc15'
  const icon = `<svg viewBox="0 0 100 72" aria-hidden="true"><path d="${feature.properties.overviewPath}" fill="${fillColor}" fill-opacity=".9" stroke="${strokeColor}" stroke-width="5" stroke-linejoin="round"/></svg>`
  const sizeScale = 1 + Math.min(Math.max(zoom - 2, 0), 5) * 0.1
  const width = (isSelected ? 25 : 21) * sizeScale
  const height = (isSelected ? 19 : 16) * sizeScale

  return divIcon({
    className: 'slick-overview-icon',
    html: icon,
    iconSize: [width, height],
    iconAnchor: [width / 2, height / 2],
  })
}

function SlickMapLayers({ selectedSlickId, onSelect, hoveredTrack }) {
  const map = useMap()
  const [zoom, setZoom] = useState(map.getZoom())
  const showOverviewIcons = zoom < 8

  useEffect(() => {
    const syncZoom = () => setZoom(map.getZoom())
    map.on('zoomend', syncZoom)
    return () => map.off('zoomend', syncZoom)
  }, [map])

  const slickStyle = (feature) => {
    const isSelected = feature.id === selectedSlickId
    return {
      color: isSelected ? '#2dd4bf' : feature.properties.color,
      weight: isSelected ? 3 : 2,
      opacity: showOverviewIcons ? 0 : isSelected ? 1 : 0.95,
      fillColor: isSelected ? '#2dd4bf' : feature.properties.color,
      fillOpacity: showOverviewIcons ? 0 : isSelected ? 0.58 : 0.34,
    }
  }

  const handleEachSlick = (feature, layer) => {
    layer.on({
      click: () => onSelect(feature.id),
    })
  }

  return (
    <>
      <GeoJSON
        key={`${selectedSlickId ?? 'none'}-${showOverviewIcons ? 'overview' : 'detail'}`}
        data={slicks}
        style={slickStyle}
        onEachFeature={handleEachSlick}
      />
      {showOverviewIcons && slicks.features.map((feature) => (
        <Marker
          key={`overview-${feature.id}`}
          position={[feature.properties.centroid.lat, feature.properties.centroid.lon]}
          icon={createSlickIcon(feature, feature.id === selectedSlickId, zoom)}
          eventHandlers={{ click: () => onSelect(feature.id) }}
        />
      ))}
      {hoveredTrack && (
        <Polyline
          positions={hoveredTrack.map((point) => [point.lat, point.lon])}
          pathOptions={{ color: '#facc15', weight: 2.5, opacity: 0.95, dashArray: '3 7', lineCap: 'round' }}
        />
      )}
    </>
  )
}

function App() {
  const [selectedSlickId, setSelectedSlickId] = useState(null)
  const [hoveredVesselMmsi, setHoveredVesselMmsi] = useState(null)
  const selectedSlick = slicks.features.find((feature) => feature.id === selectedSlickId)
  const hoveredVessel = selectedSlick?.properties.potentialSources.find((vessel) => vessel.mmsi === hoveredVesselMmsi)
  const hoveredTrack = hoveredVessel?.track ?? null

  const handleSelectSlick = (slickId) => {
    setSelectedSlickId(slickId)
    setHoveredVesselMmsi(null)
  }

  return (
    <main className="gis-app">
      <header className="gis-header">
        <div className="gis-brand">
          <span className="brand-mark" aria-hidden="true">V</span>
          <span>VARUNETRA</span>
        </div>
        <span className="header-context">Oil Spill Intelligence</span>
        <span className="header-spacer" />
        <span className="header-coordinates">MAP VIEW / GLOBAL</span>
      </header>
      <div className="workspace">
        <section className="map-region" aria-label="Oil spill map">
          <MapContainer center={[25, 15]} zoom={2} minZoom={2} scrollWheelZoom className="gis-map">
            <FitGlobalExtent positions={globalSlickPositions} />
            <TileLayer
              attribution="Tiles &copy; Esri"
              url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
            />
            <SlickMapLayers selectedSlickId={selectedSlickId} onSelect={handleSelectSlick} hoveredTrack={hoveredTrack} />
            <ScaleControl position="bottomleft" metric imperial={false} />
          </MapContainer>
          <div className="map-label">GLOBAL / SHIPPING-LANE DETECTIONS</div>
        </section>
        <aside className="details-sidebar" aria-label="Slick details sidebar">
          <div className="sidebar-heading">
            <span className="sidebar-eyebrow">Selection details</span>
            <h1>{selectedSlick ? selectedSlick.properties.slick_id : 'Slick details'}</h1>
          </div>
          {selectedSlick ? (
            <div className="sidebar-content">
              <section className="sidebar-section">
                <h2>Slick Details</h2>
                <dl className="metadata-grid">
                  <div><dt>Time of Detection</dt><dd>{new Date(selectedSlick.properties.detection_time).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })} UTC</dd></div>
                  <div><dt>Length</dt><dd>{selectedSlick.properties.length_km.toFixed(1)} km</dd></div>
                  <div><dt>Area</dt><dd>{selectedSlick.properties.area_km2.toFixed(1)} km²</dd></div>
                  <div><dt>Lat / Lon</dt><dd>{selectedSlick.properties.centroid.lat.toFixed(4)}, {selectedSlick.properties.centroid.lon.toFixed(4)}</dd></div>
                  <div><dt>S1 Scene ID</dt><dd>{selectedSlick.properties.scene_id}</dd></div>
                  <div><dt>EEZ</dt><dd>{selectedSlick.properties.eez}</dd></div>
                </dl>
              </section>
              <SlickAnalysisPanels slick={selectedSlick} onVesselPathHover={setHoveredVesselMmsi} />
            </div>
          ) : (
            <div className="sidebar-empty">
              <p>Select an oil slick on the map to inspect its detection, geometry, and source data.</p>
            </div>
          )}
          <div className="sidebar-footer">VARUNETRA / SIH 2026</div>
        </aside>
      </div>
    </main>
  )
}

export default App
