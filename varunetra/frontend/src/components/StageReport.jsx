import { useState } from 'react'

function StageReport({ scenario }) {
  const [isRevealed, setIsRevealed] = useState(false)
  const topVessel = scenario.ais_vessels.slice().sort((first, second) => first.rank - second.rank)[0]
  const { predicted_hours, uncertainty_hours, window_start_hours, window_end_hours } = scenario.age_estimate
  const { lat, lon } = scenario.backtrack.origin_center

  return (
    <section className="report-stage" aria-labelledby="report-title">
      <div className="report-action-row">
        <div>
          <span className="stage-index">CLOSING OUTPUT</span>
          <h2 id="report-title">Incident report ready for review.</h2>
        </div>
        <button type="button" className="report-button" onClick={() => setIsRevealed(true)}>
          <span>{isRevealed ? 'Report Generated' : 'Generate Incident Report'}</span>
          <span aria-hidden="true">↗</span>
        </button>
      </div>
      {isRevealed && (
        <div className="report-card">
          <div className="report-card-heading">
            <div>
              <span className="report-kicker">VARUNETRA / ANALYSIS BRIEF</span>
              <h3>Oil spill source assessment</h3>
            </div>
            <span className="report-seal">VERIFIED<br />DEMO RUN</span>
          </div>
          <div className="report-grid">
            <div><span>SCENARIO</span><strong>{scenario.name}</strong></div>
            <div><span>SPILL AGE WINDOW</span><strong>{predicted_hours}h ± {uncertainty_hours}h <small>({window_start_hours}-{window_end_hours}h)</small></strong></div>
            <div><span>PROBABLE ORIGIN</span><strong>{lat.toFixed(2)}°N / {lon.toFixed(2)}°E</strong></div>
            <div><span>TOP SUSPECT VESSEL</span><strong>{topVessel.name} <small>{Math.round(topVessel.confidence * 100)}% confidence</small></strong></div>
          </div>
          <div className="report-footer">
            <span>PRECOMPUTED SCENARIO / STATIC DEMONSTRATION</span>
            <button type="button" className="export-button" onClick={() => window.print()}>Download / Export ↗</button>
          </div>
        </div>
      )}
    </section>
  )
}

export default StageReport
