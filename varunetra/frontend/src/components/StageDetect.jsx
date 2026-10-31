function StageDetect({ scenario }) {
  return (
    <section className="stage-card stage-detect" aria-labelledby="detect-title">
      <div className="stage-heading">
        <div>
          <span className="stage-index">01 / DETECT</span>
          <h2 id="detect-title">Slick Detected</h2>
        </div>
        <span className="stage-check" aria-label="Complete">✓</span>
      </div>
      <div className="sar-viewport">
        <div className="sar-fallback" aria-hidden="true">
          <span className="fallback-contour contour-one" />
          <span className="fallback-contour contour-two" />
          <span className="fallback-slick" />
        </div>
        <img
          className="sar-image"
          src={scenario.sar_image_url}
          alt={`SAR detection image for ${scenario.name}`}
        />
        <img
          className="mask-image"
          src={scenario.mask_image_url}
          alt=""
          aria-hidden="true"
        />
        <div className="image-label image-label-top">SAR / VV POLARIZATION</div>
        <div className="image-label image-label-bottom">SEGMENTATION MASK ACTIVE</div>
        <div className="scan-line" aria-hidden="true" />
      </div>
      <div className="stage-caption">
        <span className="signal-bars" aria-hidden="true"><i /><i /><i /><i /></span>
        <span>High-confidence surface anomaly isolated from SAR scene</span>
        <span className="caption-value">{scenario.detection.area_km2.toFixed(1)} km²</span>
      </div>
    </section>
  )
}

export default StageDetect
