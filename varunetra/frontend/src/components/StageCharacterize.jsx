import { useEffect, useState } from 'react'

const stats = [
  { key: 'area_km2', label: 'Area', unit: 'km²', decimals: 1 },
  { key: 'fragmentation', label: 'Fragmentation', unit: 'clusters', decimals: 0 },
  { key: 'elongation', label: 'Elongation', unit: 'ratio', decimals: 1 },
]

function StageCharacterize({ scenario }) {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    let frameId
    const startedAt = performance.now()
    const animate = (now) => {
      const elapsed = now - startedAt
      setProgress(Math.min(elapsed / 900, 1))
      if (elapsed < 900) frameId = requestAnimationFrame(animate)
    }
    frameId = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frameId)
  }, [])

  return (
    <section className="stage-card characterize-card" aria-labelledby="characterize-title">
      <div className="stage-heading">
        <div>
          <span className="stage-index">02 / CHARACTERIZE</span>
          <h2 id="characterize-title">Geometry Extracted</h2>
        </div>
        <span className="stage-check" aria-label="Complete">✓</span>
      </div>
      <div className="stat-grid">
        {stats.map((stat) => {
          const value = scenario.detection[stat.key] * progress
          return (
            <div className="stat-card" key={stat.key}>
              <span className="stat-label">{stat.label}</span>
              <strong>{value.toFixed(stat.decimals)}</strong>
              <span className="stat-unit">{stat.unit}</span>
              <span className="stat-rule" aria-hidden="true" />
              <span className="stat-status">CALCULATED</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

export default StageCharacterize
