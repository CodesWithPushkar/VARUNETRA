function createRandom(seed) {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 4294967296
  }
}

function isInsidePolygon(point, polygon) {
  let inside = false
  for (let index = 0, previousIndex = polygon.length - 1; index < polygon.length; previousIndex = index, index += 1) {
    const [x, y] = point
    const [currentX, currentY] = polygon[index]
    const [previousX, previousY] = polygon[previousIndex]
    const crosses = currentY > y !== previousY > y
    const intersectionX = ((previousX - currentX) * (y - currentY)) / (previousY - currentY || 1e-8) + currentX
    if (crosses && x < intersectionX) inside = !inside
  }
  return inside
}

function createHindcastDots(slickId, netDriftAngle, uncertaintySpread) {
  const seed = [...slickId].reduce((hash, character) => Math.imul(hash ^ character.charCodeAt(0), 16777619), 2166136261)
  const random = createRandom(seed)
  const uncertaintyPolygon = [
    [145, 130], [159, 96], [185, 70], [219, 62], [251, 72], [283, 91],
    [302, 121], [294, 151], [271, 177], [241, 197], [208, 194], [178, 180], [153, 156],
  ]
  const dots = []

  while (dots.length < 42) {
    const angle = random() * Math.PI * 2
    const radius = Math.sqrt(random())
    const candidate = [220 + Math.cos(angle) * 74 * radius, 131 + Math.sin(angle) * 57 * radius]
    if (isInsidePolygon(candidate, uncertaintyPolygon)) dots.push(candidate)
  }

  const spreadScale = Math.min(Math.max(uncertaintySpread / 26, 0.72), 1.48)
  const shapeTransform = `translate(220 131) rotate(${netDriftAngle} 0 0) scale(${spreadScale}) translate(-220 -131)`
  return { dots, uncertaintyPolygon, shapeTransform }
}

function starPoints(centerX, centerY, outerRadius, innerRadius) {
  return Array.from({ length: 10 }, (_, index) => {
    const angle = -Math.PI / 2 + (index * Math.PI) / 5
    const radius = index % 2 === 0 ? outerRadius : innerRadius
    return `${(centerX + Math.cos(angle) * radius).toFixed(1)},${(centerY + Math.sin(angle) * radius).toFixed(1)}`
  }).join(' ')
}

function createForceVector(directionDegrees, length) {
  const radians = directionDegrees * Math.PI / 180
  const endX = 220 + Math.sin(radians) * length
  const endY = 131 - Math.cos(radians) * length
  const directionX = (endX - 220) / length
  const directionY = (endY - 131) / length
  const baseX = endX - directionX * 7
  const baseY = endY - directionY * 7
  const sideX = -directionY * 3.2
  const sideY = directionX * 3.2
  return {
    endX,
    endY,
    arrowPoints: `${endX},${endY} ${baseX + sideX},${baseY + sideY} ${baseX - sideX},${baseY - sideY}`,
  }
}

function GeometryExtracted({ properties }) {
  const regionLayouts = {
    1: [{ x: 165, y: 83, scale: 1.2 }],
    2: [{ x: 102, y: 83, scale: 0.78 }, { x: 231, y: 103, scale: 0.78 }],
    3: [{ x: 76, y: 77, scale: 0.55 }, { x: 166, y: 68, scale: 0.55 }, { x: 254, y: 78, scale: 0.55 }],
    4: [{ x: 72, y: 63, scale: 0.48 }, { x: 148, y: 63, scale: 0.48 }, { x: 224, y: 63, scale: 0.48 }, { x: 111, y: 124, scale: 0.48 }],
  }
  const layout = regionLayouts[properties.geometryRegions.length] ?? regionLayouts[4]

  return (
    <section className="sidebar-section geometry-section">
      <h2>Geometry Extracted</h2>
      <dl className="geometry-metrics">
        <div><dt>Area</dt><dd>{properties.area_km2.toFixed(1)} km²</dd></div>
        <div><dt>Fragmentation</dt><dd>{properties.fragmentation} clusters</dd></div>
        <div><dt>Elongation ratio</dt><dd>{properties.elongation.toFixed(1)}</dd></div>
      </dl>
      <div className="geometry-region-plot">
        <svg viewBox="0 0 320 192" role="img" aria-label={`Extracted spill regions for ${properties.geometryRegions.length} geometry clusters`}>
          <rect width="320" height="192" className="geometry-plot-background" />
          {[72, 120, 168, 216, 264].map((x) => <line key={`x-${x}`} x1={x} y1="14" x2={x} y2="158" className="geometry-plot-grid" />)}
          {[38, 78, 118, 158].map((y) => <line key={`y-${y}`} x1="32" y1={y} x2="304" y2={y} className="geometry-plot-grid" />)}
          <line x1="32" y1="158" x2="304" y2="158" className="geometry-plot-axis" />
          <line x1="32" y1="14" x2="32" y2="158" className="geometry-plot-axis" />
          <text x="72" y="174" className="geometry-plot-tick">0</text>
          <text x="168" y="174" className="geometry-plot-tick">10</text>
          <text x="264" y="174" className="geometry-plot-tick">20 km</text>
          <text x="13" y="88" className="geometry-plot-axis-label" transform="rotate(-90 13 88)">CROSS-TRACK</text>
          {properties.geometryRegions.map((region, index) => {
            const regionLayout = layout[index]
            return (
              <g key={region.id}>
                <g transform={`translate(${regionLayout.x} ${regionLayout.y}) scale(${regionLayout.scale}) translate(-50 -35)`}>
                  <path d={region.path} fill={region.color} fillOpacity=".35" stroke={region.color} strokeWidth="2" strokeLinejoin="round" />
                </g>
                <text x={regionLayout.x} y={regionLayout.y + regionLayout.scale * 34 + 8} className="geometry-plot-region-label">R{index + 1}</text>
              </g>
            )
          })}
        </svg>
      </div>
      <div className="geometry-region-legend">
        {properties.geometryRegions.map((region) => (
          <span key={region.id}><i style={{ background: region.color }} />{region.name} / {region.area_km2.toFixed(1)} km²</span>
        ))}
      </div>
    </section>
  )
}

function SpillAgeEstimated({ properties }) {
  const markerPosition = `${Math.min((properties.age_hours / 96) * 100, 100)}%`
  const exposureEstimate = properties.age_hours >= 24 ? 'Indicates >24h exposure' : 'Indicates <24h exposure'
  const emulsificationNote = {
    High: 'Strong water-in-oil emulsion signature',
    Medium: 'Water-in-oil emulsion detected',
    Low: 'Limited emulsion evidence',
  }[properties.weathering.emulsification]
  const backscatterDecay = (-(1 + properties.age_hours * 0.075)).toFixed(1)

  return (
    <section className="sidebar-section age-section">
      <h2>Spill Age Estimated</h2>
      <div className="age-value">
        <strong>{properties.age_hours.toFixed(1)}h</strong>
        <span>± {properties.age_uncertainty_hours.toFixed(1)}h</span>
      </div>
      <div className="age-model-labels"><span>WEATHERING MODEL</span><span>0-96 HOURS</span></div>
      <div className="age-timeline" role="img" aria-label={`Estimated spill age ${properties.age_hours.toFixed(1)} hours`}>
        <div className="age-timeline-track" />
        <span className="age-timeline-marker" style={{ left: markerPosition }} />
      </div>
      <div className="weathering-state-labels"><span>Fresh</span><span>Emulsified</span><span>Weathered</span></div>
      <div className="age-axis-labels"><span>0h</span><span>24h</span><span>48h</span><span>72h</span><span>96h</span></div>
      <div className="reasoning-log" aria-label="Algorithm reasoning">
        <div>&gt; EVAPORATION LOSS: {properties.weathering.evaporationLoss.toFixed(1)}% ({exposureEstimate})</div>
        <div>&gt; EMULSIFICATION: {properties.weathering.emulsification} ({emulsificationNote})</div>
        <div>&gt; BACKSCATTER DECAY: {backscatterDecay}dB (Consistent with {Math.round(properties.age_hours)}h weathering)</div>
      </div>
    </section>
  )
}

function BacktrackTrajectory({ slickId, properties }) {
  const { trajectory, environment } = properties
  const { dots, uncertaintyPolygon, shapeTransform } = createHindcastDots(
    slickId,
    trajectory.netDriftAngle,
    trajectory.uncertaintySpread,
  )
  const uncertaintyPoints = uncertaintyPolygon.map(([x, y]) => `${x},${y}`).join(' ')
  const windVector = createForceVector(environment.windDirection, 25 + environment.windSpeed * 0.55)
  const currentVector = createForceVector(environment.currentDirection, 25 + environment.currentSpeed * 11)
  const netVector = createForceVector(trajectory.netDriftAngle, 67)

  return (
    <section className="sidebar-section hindcast-section">
      <h2>Backtrack Trajectory</h2>
      <div className="hindcast-chart">
        <svg viewBox="0 0 420 256" role="img" aria-label={`Hindcast plot with observed slick, ${dots.length} backtracked positions, origin uncertainty, and force vectors`}>
          <rect width="420" height="256" fill="#f8fafc" />
          {[65, 110, 155, 200].map((y) => <line key={`horizontal-${y}`} x1="34" y1={y} x2="300" y2={y} className="hindcast-gridline" />)}
          {[78, 122, 166, 210, 254].map((x) => <line key={`vertical-${x}`} x1={x} y1="38" x2={x} y2="218" className="hindcast-gridline" />)}
          <line x1="34" y1="218" x2="300" y2="218" className="hindcast-axis" />
          <line x1="34" y1="38" x2="34" y2="218" className="hindcast-axis" />
          <text x="160" y="245" className="hindcast-axis-label">LONGITUDE / RELATIVE DISTANCE</text>
          <text x="12" y="133" className="hindcast-axis-label" transform="rotate(-90 12 133)">LATITUDE</text>
          <g transform={shapeTransform}>
            <polygon points={uncertaintyPoints} className="uncertainty-region" />
            {dots.map(([x, y], index) => <circle key={`position-${index}`} cx={x} cy={y} r="2.1" className="backtrack-dot" />)}
          </g>
          <path d="M48 154 59 127 81 115 105 119 121 137 116 159 94 174 68 169Z" className="observed-slick" />
          <line x1="220" y1="131" x2={windVector.endX} y2={windVector.endY} className="force-vector wind-vector" />
          <polygon points={windVector.arrowPoints} className="force-arrow wind-arrow" />
          <line x1="220" y1="131" x2={currentVector.endX} y2={currentVector.endY} className="force-vector current-vector" />
          <polygon points={currentVector.arrowPoints} className="force-arrow current-arrow" />
          <line x1="220" y1="131" x2={netVector.endX} y2={netVector.endY} className="force-vector net-vector" />
          <polygon points={netVector.arrowPoints} className="force-arrow net-arrow" />
          <polygon points={starPoints(220, 131, 10, 4.4)} className="origin-centroid" />
          <g className="hindcast-legend">
            <rect x="285" y="12" width="127" height="139" rx="2" />
            <path d="M293 23h13v8h-13z" className="legend-observed" /><text x="311" y="30">Observed spill</text>
            <path d="M293 40h13v8h-13z" className="legend-uncertainty" /><text x="311" y="47">Origin uncertainty</text>
            <circle cx="299.5" cy="60" r="2.4" className="legend-dot-sample" /><text x="311" y="63">Backtracked positions</text>
            <polygon points={starPoints(299.5, 78, 6, 2.7)} className="legend-star-sample" /><text x="311" y="81">Origin centroid</text>
            <line x1="293" y1="96" x2="306" y2="96" className="legend-vector wind-vector" /><polygon points="306,96 302,93 302,99" className="legend-arrow wind-arrow" /><text x="311" y="98">Wind force</text>
            <line x1="293" y1="113" x2="306" y2="113" className="legend-vector current-vector" /><polygon points="306,113 302,110 302,116" className="legend-arrow current-arrow" /><text x="311" y="115">Ocean current</text>
            <line x1="293" y1="130" x2="306" y2="130" className="legend-vector net-vector" /><polygon points="306,130 302,126 302,134" className="legend-arrow net-arrow" /><text x="311" y="132">Net drift</text>
          </g>
        </svg>
      </div>
      <div className="force-readout">
        <div><span>WIND</span><strong>{environment.windSpeed.toFixed(1)} kn / {environment.windDirection}°</strong></div>
        <div><span>CURRENT</span><strong>{environment.currentSpeed.toFixed(2)} m/s / {environment.currentDirection}°</strong></div>
        <div><span>NET DRIFT</span><strong>{trajectory.netDriftAngle.toFixed(1)}° / ±{trajectory.uncertaintySpread.toFixed(1)} km</strong></div>
      </div>
    </section>
  )
}

function PotentialSources({ sources, onPathHover }) {
  const rankedSources = sources.slice().sort((first, second) => second.matchScore - first.matchScore)

  return (
    <section className="sidebar-section sources-section">
      <h2>Potential Sources (AIS)</h2>
      <div className="source-list">
        {rankedSources.map((vessel, index) => (
          <article className="source-card" key={`${vessel.mmsi}-${vessel.name}`}>
            <div className={`source-score ${vessel.matchScore > 90 ? 'source-score-high' : 'source-score-standard'}`}>
              <strong>{vessel.matchScore}<small>%</small></strong>
              <span>match</span>
            </div>
            <div className="source-details">
              <span className="source-rank">CANDIDATE 0{index + 1}</span>
              <strong className="source-name">{vessel.name}</strong>
              <span className="source-type">{vessel.type} / {vessel.flag}</span>
              <span className="source-mmsi">MMSI {vessel.mmsi}</span>
              <button
                type="button"
                className="view-path-button"
                aria-label={`View path for ${vessel.name}`}
                onMouseEnter={() => onPathHover(vessel.mmsi)}
                onMouseLeave={() => onPathHover(null)}
                onFocus={() => onPathHover(vessel.mmsi)}
                onBlur={() => onPathHover(null)}
              >
                View Path <span aria-hidden="true">↗</span>
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}

function SlickAnalysisPanels({ slick, onVesselPathHover }) {
  return (
    <>
      <GeometryExtracted properties={slick.properties} />
      <SpillAgeEstimated properties={slick.properties} />
      <BacktrackTrajectory slickId={slick.id} properties={slick.properties} />
      <PotentialSources sources={slick.properties.potentialSources} onPathHover={onVesselPathHover} />
    </>
  )
}

export default SlickAnalysisPanels
