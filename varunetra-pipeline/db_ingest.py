#!/usr/bin/env python3
"""
VARUNETRA Database Ingestion Script (db_ingest.py)
Pushes aged oil slick GeoJSON features to Neon PostgreSQL.

Schema Compliance:
- Extracts geometry as stringified JSON for 'polygon_geojson' column
- Injects safe defaults for all NOT NULL columns
- Compatible with Node.js/Prisma backend expectations
"""

import json
import logging
import os
import sys
import uuid
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def load_geojson(geojson_path: str) -> dict:
    """Load GeoJSON FeatureCollection from file."""
    geojson_path = Path(geojson_path)
    if not geojson_path.exists():
        logger.error(f"GeoJSON file not found: {geojson_path}")
        sys.exit(1)
    
    try:
        with open(geojson_path, 'r') as f:
            data = json.load(f)
        logger.info(f"Loaded GeoJSON from {geojson_path}")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {geojson_path}: {e}")
        sys.exit(1)


def validate_database_url() -> str:
    """Validate DATABASE_URL is available from environment."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not found in environment. Load .env with python-dotenv.")
        sys.exit(1)
    logger.info("DATABASE_URL loaded from environment")
    return db_url


def connect_database(db_url: str):
    """Establish PostgreSQL connection."""
    try:
        conn = psycopg2.connect(db_url)
        logger.info("Connected to Neon PostgreSQL")
        return conn
    except psycopg2.DatabaseError as e:
        logger.error(f"Database connection failed: {e}")
        sys.exit(1)


def extract_feature_properties(feature: dict) -> dict:
    """
    Extract and validate properties from a GeoJSON feature.
    Maps pipeline property names to database schema.
    
    Property Mapping:
    - age_hours: from feature['properties']['est_age_hr'] (Stage 5)
    - detection_confidence: from feature['properties']['mean_prob'] (Stage 2)
    - spill_id: generated if missing
    - area_km2: from feature['properties']['area_km2']
    """
    props = feature.get("properties", {})
    
    # Generate spill_id if missing (backend compatibility)
    spill_id = props.get("spill_id") or str(uuid.uuid4())
    
    # Extract measurement properties using correct pipeline field names
    # Stage 5 (estimate_age.py) outputs 'est_age_hr', not 'age_hours'
    age_hours = props.get("est_age_hr") or props.get("age_hours") or 0.0
    
    # Stage 2+ (preprocessing) outputs 'mean_prob', not 'detection_confidence'
    detection_confidence = props.get("mean_prob") or props.get("detection_confidence") or 0.5
    
    # Area is consistent across stages
    area_km2 = props.get("area_km2") or 0.0
    
    # Validate numeric types
    try:
        area_km2 = float(area_km2) if area_km2 is not None else 0.0
        age_hours = float(age_hours) if age_hours is not None else 0.0
        detection_confidence = float(detection_confidence) if detection_confidence is not None else 0.5
    except (ValueError, TypeError) as e:
        logger.warning(f"Property type conversion failed for feature {spill_id}: {e}")
        area_km2, age_hours, detection_confidence = 0.0, 0.0, 0.5
    
    # Clamp detection_confidence to [0, 1]
    detection_confidence = max(0.0, min(1.0, detection_confidence))
    
    logger.debug(f"Extracted properties - spill_id: {spill_id}, area_km2: {area_km2}, age_hr: {age_hours}, confidence: {detection_confidence}")
    
    return {
        "spill_id": spill_id,
        "area_km2": area_km2,
        "age_hours": age_hours,
        "detection_confidence": detection_confidence
    }


def build_insert_record(
    feature: dict,
    scene_time: str
) -> dict:
    """
    Build complete database record from GeoJSON feature.
    Applies all safe defaults for NOT NULL columns.
    """
    props = extract_feature_properties(feature)
    
    # Extract and stringify geometry (critical for backend compatibility)
    geometry = feature.get("geometry")
    if not geometry:
        logger.warning(f"Feature {props['spill_id']} has no geometry, skipping")
        return None
    
    polygon_geojson = json.dumps(geometry)
    
    record = {
        "spill_id": props["spill_id"],
        "polygon_geojson": polygon_geojson,
        "area_km2": props["area_km2"],
        "age_hours": props["age_hours"],
        "detection_confidence": props["detection_confidence"],
        # Safe defaults for schema NOT NULL constraints
        "kind": "new",
        "image_time_utc": scene_time,
        "origin_lat": 0.0,
        "origin_lon": 0.0,
        "origin_radius_km": 0.0,
        "window_start_utc": scene_time,
        "window_end_utc": scene_time,
        "created_utc": datetime.now(timezone.utc).isoformat()
    }
    
    return record


def insert_features_to_database(
    conn,
    features: list,
    scene_time: str
) -> int:
    """
    Insert all features into spills table with duplicate handling.
    Uses ON CONFLICT (spill_id) DO NOTHING to safely skip duplicates.
    Commits per-row to prevent batch rollback on single failures.
    
    Returns count of successfully inserted records.
    """
    cursor = conn.cursor()
    inserted_count = 0
    skipped_count = 0
    failed_count = 0
    
    for idx, feature in enumerate(features, start=1):
        record = build_insert_record(feature, scene_time)
        if not record:
            failed_count += 1
            continue
        
        try:
            # Use ON CONFLICT to safely handle duplicate spill_ids
            # DO NOTHING: Skip if spill_id already exists (idempotent)
            insert_query = """
            INSERT INTO spills (
                spill_id, polygon_geojson, area_km2, age_hours, detection_confidence,
                kind, image_time_utc, origin_lat, origin_lon, origin_radius_km,
                window_start_utc, window_end_utc, created_utc
            ) VALUES (
                %(spill_id)s, %(polygon_geojson)s, %(area_km2)s, %(age_hours)s,
                %(detection_confidence)s, %(kind)s, %(image_time_utc)s, %(origin_lat)s,
                %(origin_lon)s, %(origin_radius_km)s, %(window_start_utc)s,
                %(window_end_utc)s, %(created_utc)s
            )
            ON CONFLICT (spill_id) DO NOTHING
            """
            cursor.execute(insert_query, record)
            
            # Check if row was actually inserted (affected rows > 0)
            if cursor.rowcount > 0:
                inserted_count += 1
            else:
                # Duplicate spill_id, skipped by ON CONFLICT
                skipped_count += 1
                logger.debug(f"Skipped duplicate spill_id: {record['spill_id']}")
            
            # Commit per-row to prevent batch rollback on integrity errors
            conn.commit()
            
            if idx % 10 == 0:
                logger.info(f"Processed {idx} features ({inserted_count} inserted, {skipped_count} skipped)...")
        
        except psycopg2.IntegrityError as e:
            # Integrity constraint (not primary key) violation
            logger.warning(f"Integrity constraint error for spill {record['spill_id']}: {e}")
            conn.rollback()
            failed_count += 1
        except psycopg2.Error as e:
            # Other database errors (column type, NOT NULL, etc.)
            logger.error(f"Database error for spill {record['spill_id']}: {e}")
            conn.rollback()
            failed_count += 1
    
    cursor.close()
    
    logger.info(
        f"Database ingestion complete: "
        f"{inserted_count} inserted, {skipped_count} skipped (duplicates), {failed_count} failed"
    )
    
    return inserted_count


def parse_arguments():
    """Parse command-line arguments."""
    parser = ArgumentParser(
        description="Ingest aged oil slick GeoJSON into VARUNETRA Neon PostgreSQL database"
    )
    parser.add_argument(
        "--geojson-path",
        default="stage-5/aged_slicks.geojson",
        help="Path to aged slicks GeoJSON file (default: stage-5/aged_slicks.geojson)"
    )
    parser.add_argument(
        "--scene-time",
        required=True,
        help="Scene acquisition time (ISO 8601 format, e.g., 2024-09-06T18:10:20Z)"
    )
    return parser.parse_args()


def validate_scene_time(scene_time: str):
    """Validate scene_time is valid ISO 8601 format."""
    try:
        datetime.fromisoformat(scene_time.replace("Z", "+00:00"))
    except ValueError:
        logger.error(f"Invalid scene_time format: {scene_time}. Use ISO 8601 (e.g., 2024-09-06T18:10:20Z)")
        sys.exit(1)


def main():
    """Main ingestion workflow."""
    args = parse_arguments()
    
    logger.info("="*70)
    logger.info("VARUNETRA DATABASE INGESTION")
    logger.info("="*70)
    
    # Validate inputs
    validate_scene_time(args.scene_time)
    logger.info(f"Scene time: {args.scene_time}")
    
    # Load environment
    load_dotenv()
    
    # Load GeoJSON
    logger.info(f"Loading GeoJSON: {args.geojson_path}")
    geojson_data = load_geojson(args.geojson_path)
    features = geojson_data.get("features", [])
    
    if not features:
        logger.warning(f"No features found in {args.geojson_path}")
        logger.info("Ingestion skipped (no data to insert)")
        return
    
    logger.info(f"Loaded {len(features)} features from GeoJSON")
    logger.info("")
    
    # Connect and ingest
    db_url = validate_database_url()
    conn = connect_database(db_url)
    
    try:
        inserted = insert_features_to_database(conn, features, args.scene_time)
        logger.info("")
        if inserted > 0:
            logger.info(f"✓ Successfully ingested {inserted} oil slicks to VARUNETRA database")
        else:
            logger.warning("⚠ No new features were inserted (possible duplicates)")
    except Exception as e:
        logger.error(f"Fatal error during ingestion: {e}")
        sys.exit(1)
    finally:
        conn.close()
        logger.info("Database connection closed")
        logger.info("="*70)


if __name__ == "__main__":
    main()
