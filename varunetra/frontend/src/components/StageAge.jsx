import { useEffect, useState } from 'react'

function StageAge({ scenario }) {
  const [isAnalyzing, setIsAnalyzing] = useState(true)

  useEffect(() => {
    const timeoutId = setTimeout(() => setIsAnalyzing(false), 1500)
    return () => clearTimeout(timeoutId)
  }, [])

  const { predicted_hours, uncertainty_hours, window_start_hours, window_end_hours } = scenario.age_estimate
  const windowStart = `${(window_start_hours / 96) * 100}%`
  const windowWidth = `${((window_end_hours - window_start_hours) / 96) * 100}%`

  return (
    <section className="stage-card age-card" aria-labelledby="age-title">
      <div className="stage-heading">
        <div>
          <span className="stage-index">03 / TRACE (AGE)</span>
          <h2 id="age-title">Spill Age Estimated — Weathering Model</h2>
        </div>
        {!isAnalyzing && <span className="stage-check" aria-label="Complete">✓</span>}
      </div>
      {isAnalyzing ? (
        <div className="age-loading" role="status">
          <span className="loading-ring" aria-hidden="true" />
          <span>Analyzing weathering state...</span>
          <small>CLASSIFYING SURFACE TRANSFORMATION</small>
        </div>
      ) : (
        <div className="age-result">
          <div className="age-hero-value">
            <span className="age-number">{predicted_hours}h</span>
            <span className="age-uncertainty">± {uncertainty_hours}h</span>
          </div>
          <div className="timeline-wrap">
            <div className="timeline-labels"><span>0h</span><span>48h</span><span>96h</span></div>
            <div className="timeline-axis" aria-label={`Estimated age window from ${window_start_hours} to ${window_end_hours} hours`}>
              <span className="industry-band" />
              <span className="estimate-band" style={{ left: windowStart, width: windowWidth }} />
              <span className="estimate-marker" style={{ left: `${(predicted_hours / 96) * 100}%` }} />
            </div>
            <div className="timeline-legend">
              <span><i className="legend-swatch estimate-swatch" /> MODEL WINDOW / {window_start_hours}-{window_end_hours}H</span>
              <span><i className="legend-swatch industry-swatch" /> INDUSTRY STANDARD / 24-72H</span>
            </div>
          </div>
          <div className="comparison-callout">
            <span className="callout-mark">≈</span>
            <span>vs. industry-standard <strong>1–3 day</strong> (24–72h) estimate</span>
            <span className="callout-detail">NARROWER CONFIDENCE WINDOW</span>
          </div>
        </div>
      )}
    </section>
  )
}

export default StageAge
