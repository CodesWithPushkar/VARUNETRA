import { useState } from 'react'
import scenarioData from '../data/scenarios.json'

function Landing({ onRunAnalysis }) {
  const [selectedScenarioId, setSelectedScenarioId] = useState(
    scenarioData.scenarios[0]?.id ?? '',
  )

  const handleRunAnalysis = () => {
    onRunAnalysis(selectedScenarioId)
  }

  return (
    <main className="landing-shell">
      <div className="ocean-grid" aria-hidden="true" />
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">V</span>
          <span>VARUNETRA</span>
        </div>
        <span className="system-status"><span className="status-dot" /> Prototype / Offline Mode</span>
      </header>

      <section className="landing-content" aria-labelledby="page-title">
        <div className="eyebrow"><span /> Satellite Intelligence Platform</div>
        <p className="devanagari">वरुणेत्र</p>
        <h1 id="page-title">Sentinels of<br /><em>The Ocean</em></h1>
        <p className="intro-copy">
          Detect oil spills from satellite SAR imagery, reconstruct their origin,
          and identify the vessel most likely responsible.
        </p>

        <div className="run-panel">
          <label htmlFor="scenario-select">Select monitoring scenario</label>
          <div className="control-row">
            <div className="select-wrap">
              <select
                id="scenario-select"
                value={selectedScenarioId}
                onChange={(event) => setSelectedScenarioId(event.target.value)}
              >
                {scenarioData.scenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                  </option>
                ))}
              </select>
              <span className="select-chevron" aria-hidden="true">⌄</span>
            </div>
            <button type="button" className="run-button" onClick={handleRunAnalysis}>
              <span>Run Analysis</span>
              <span className="button-arrow" aria-hidden="true">↗</span>
            </button>
          </div>
          <div className="panel-meta">
            <span><span className="meta-icon">◈</span> PRECOMPUTED SCENARIO</span>
            <span>SCENARIO ID: {selectedScenarioId.toUpperCase()}</span>
          </div>
        </div>
      </section>

      <footer className="landing-footer">
        <span>SIH 2026 / SIH26143</span>
        <span>Ocean intelligence, made observable.</span>
      </footer>
    </main>
  )
}

export default Landing
