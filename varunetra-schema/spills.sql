CREATE TABLE IF NOT EXISTS spills (
    spill_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    image_time_utc TEXT NOT NULL,
    scene_name TEXT,

    polygon_geojson TEXT NOT NULL,

    centroid_lat DOUBLE PRECISION,
    centroid_lon DOUBLE PRECISION,
    area_km2 DOUBLE PRECISION,

    detection_confidence DOUBLE PRECISION,
    model_version TEXT,

    age_hours DOUBLE PRECISION,
    age_sigma_hours DOUBLE PRECISION,

    origin_lat DOUBLE PRECISION NOT NULL,
    origin_lon DOUBLE PRECISION NOT NULL,
    origin_radius_km DOUBLE PRECISION NOT NULL,

    origin_source TEXT,
    origin_heatmap_path TEXT,

    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,

    window_source TEXT,
    data_source TEXT,
    notes TEXT,

    created_utc TEXT NOT NULL
);