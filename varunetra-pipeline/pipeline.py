#!/usr/bin/env python3
"""PART 1: download the newest Sentinel-1 scene for each CSV target, detect and filter slicks,
and store them in the database. Stages 5-9 run separately in part2_run.py."""

import csv
import json
import logging
import os
import re
import subprocess
import sys
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent
REQUIRED_COLUMNS = {
    "location_name",
    "min_lon",
    "min_lat",
    "max_lon",
    "max_lat",
    "start_date",
    "end_date",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PIPELINE] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Runs the download and processing stages for a list of target areas."""

    def __init__(self) -> None:
        self.project_root = PIPELINE_DIR
        self.input_zip: Path | None = None
        self.scene_time: str | None = None
        self.location_name = ""
        load_dotenv(self.project_root / ".env")

    def _run_subprocess(self, step_name: str, command: list[str]) -> bool:
        logger.info("[%s] %s", self.location_name, step_name)
        try:
            result = subprocess.run(
                command,
                cwd=self.project_root,
                check=False,
                text=True,
            )
        except OSError:
            logger.exception("[%s] Could not start %s", self.location_name, step_name)
            return False

        if result.returncode:
            logger.error(
                "[%s] %s failed (exit %d). See stage output above.",
                self.location_name,
                step_name,
                result.returncode,
            )
            return False
        return True

    def download_scene(self, target: dict[str, str]) -> bool:
        bbox = ",".join(
            (target.get(key) or "").strip()
            for key in ("min_lon", "min_lat", "max_lon", "max_lat")
        )
        command = [
            sys.executable,
            str(self.project_root / "stage-1" / "download_sar.py"),
            "--bbox",
            bbox,
            "--start-date",
            target["start_date"].strip(),
            "--end-date",
            target["end_date"].strip(),
        ]
        logger.info("[%s] Searching CDSE for the newest matching IW GRD scene", self.location_name)
        try:
            result = subprocess.run(
                command,
                cwd=self.project_root,
                check=False,
                text=True,
                capture_output=True,
            )
        except OSError:
            logger.exception("[%s] Could not start Sentinel-1 downloader", self.location_name)
            return False

        if result.returncode:
            logger.error(
                "[%s] Download failed (exit %d): %s",
                self.location_name,
                result.returncode,
                result.stderr.strip() or "No error details were reported.",
            )
            return False

        try:
            downloaded = json.loads(result.stdout)
            zip_path = Path(downloaded["zip_path"]).resolve(strict=True)
            scene_time = downloaded["scene_time"]
            datetime.fromisoformat(scene_time.replace("Z", "+00:00"))
            if not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
                raise ValueError("Downloader did not return an existing ZIP file.")
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as error:
            logger.error(
                "[%s] Downloader returned invalid result: %s; stderr: %s",
                self.location_name,
                error,
                result.stderr.strip() or "(empty)",
            )
            return False

        self.input_zip = zip_path
        self.scene_time = scene_time
        logger.info(
            "[%s] Download ready: %s (scene_time=%s)",
            self.location_name,
            zip_path,
            scene_time,
        )
        return True

    def _ensure_output_directories(self) -> None:
        for directory in ("out", "sweep", "stage-4", "stage-5"):
            (self.project_root / directory).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _snap_resource_options() -> list[str]:
        memory = os.getenv("SNAP_MEMORY", "8G").strip()
        cache_size = os.getenv("SNAP_CACHE_SIZE", "5000M").strip()
        parallelism = os.getenv("SNAP_PARALLELISM", "8").strip()

        if not re.fullmatch(r"\d+[kKmMgG]", memory):
            raise ValueError("SNAP_MEMORY must be a positive size such as 8G or 5000M.")
        if not re.fullmatch(r"\d+[kKmMgG]", cache_size):
            raise ValueError("SNAP_CACHE_SIZE must be a positive size such as 5000M or 5G.")
        if not parallelism.isdecimal() or int(parallelism) < 1:
            raise ValueError("SNAP_PARALLELISM must be a positive integer.")

        return [
            f"--memory=-J-Xmx{memory}",
            f"--cache-size={cache_size}",
            f"--parallelism={parallelism}",
        ]

    def _check_file_exists(self, relative_path: str) -> bool:
        path = self.project_root / relative_path
        if not path.is_file():
            logger.error("[%s] Expected output file missing: %s", self.location_name, path)
            return False
        return True

    def run_scene_pipeline(self) -> bool:
        if self.input_zip is None or self.scene_time is None:
            logger.error("[%s] No downloaded scene is available to process.", self.location_name)
            return False

        self._ensure_output_directories()
        steps: list[tuple[str, list[str], str | None]] = [
            (
                "SNAP preprocessing",
                [
                    sys.executable,
                    str(self.project_root / "stage-2" / "preprocess_snap.py"),
                    str(self.input_zip),
                    "out/scene.tif",
                    "--extract-zip",
                    *self._snap_resource_options(),
                    "--overwrite",
                ],
                "out/scene.tif",
            ),
            (
                "Oil-slick extraction",
                [
                    sys.executable,
                    str(self.project_root / "sweep" / "sweep_scene.py"),
                    "out/scene.tif",
                    "--scene-time",
                    self.scene_time,
                ],
                "sweep/detections_median_fill.geojson",
            ),
            (
                "False-positive filtering",
                [
                    sys.executable,
                    str(self.project_root / "stage-4" / "filter_false_positives.py"),
                    "--input",
                    "sweep/detections_median_fill.geojson",
                    "--out-geojson",
                    "stage-4/final_slicks.geojson",
                    "--out-csv",
                    "stage-4/final_slicks.csv",
                    "--audit-csv",
                    "stage-4/filter_audit.csv",
                    "--cache",
                    "stage-4/wind_cache.json",
                ],
                "stage-4/final_slicks.geojson",
            ),
            (
                "Store slicks in database (part 1 end)",
                [
                    sys.executable,
                    str(self.project_root / "store_slicks.py"),
                    "--input",
                    "stage-4/final_slicks.geojson",
                ],
                None,
            ),
        ]

        for step_name, command, expected_output in steps:
            if not self._run_subprocess(step_name, command):
                return False
            if expected_output and not self._check_file_exists(expected_output):
                return False
        return True

    def run_targets(self, targets_csv: Path) -> bool:
        try:
            with targets_csv.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                columns = set(reader.fieldnames or [])
                missing_columns = REQUIRED_COLUMNS - columns
                if missing_columns:
                    logger.error("Targets CSV is missing columns: %s", ", ".join(sorted(missing_columns)))
                    return False
                targets = list(reader)
        except (OSError, csv.Error) as error:
            logger.error("Could not read targets CSV %s: %s", targets_csv, error)
            return False

        if not targets:
            logger.error("Targets CSV contains no target rows: %s", targets_csv)
            return False

        failures = 0
        for row_number, target in enumerate(targets, start=2):
            self.location_name = target.get("location_name", "").strip() or f"CSV row {row_number}"
            logger.info("=" * 72)
            logger.info("Processing %s (CSV row %d)", self.location_name, row_number)
            try:
                if not all((target.get(column) or "").strip() for column in REQUIRED_COLUMNS):
                    raise ValueError("One or more required fields are empty.")
                if not self.download_scene(target):
                    failures += 1
                    continue
                if not self.run_scene_pipeline():
                    failures += 1
            except (OSError, subprocess.SubprocessError, TypeError, ValueError):
                logger.exception("[%s] Target processing failed; moving to the next row.", self.location_name)
                failures += 1

        if failures:
            logger.error("Batch finished with %d failed target(s) out of %d.", failures, len(targets))
            return False
        logger.info("Batch completed successfully for all %d target(s).", len(targets))
        logger.info("PART 1 FINISHED. Slicks are in the database. Run part 2 with: python part2_run.py")
        return True


def parse_arguments():
    parser = ArgumentParser(
        description="Download and process Sentinel-1 IW GRD scenes for CSV targets."
    )
    parser.add_argument(
        "--targets-csv",
        default=str(PIPELINE_DIR / "targets.csv"),
        help="CSV file with location bounds and acquisition date ranges.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    targets_csv = Path(args.targets_csv).expanduser()
    if not targets_csv.is_absolute():
        targets_csv = (Path.cwd() / targets_csv).resolve()
    orchestrator = PipelineOrchestrator()
    return 0 if orchestrator.run_targets(targets_csv) else 1


if __name__ == "__main__":
    raise SystemExit(main())
