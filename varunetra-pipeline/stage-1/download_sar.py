#!/usr/bin/env python3
"""Download the newest matching Sentinel-1 IW GRD product from CDSE."""

import argparse
import json
import logging
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parents[1]
TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/"
    "auth/realms/CDSE/protocol/openid-connect/token"
)
CATALOGUE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD_URL = "https://download.dataspace.copernicus.eu/odata/v1/Products"
REQUEST_TIMEOUT = 90
CHUNK_SIZE = 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = (float(part.strip()) for part in value.split(","))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(
            "Bounding box must be west,south,east,north."
        ) from error
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise argparse.ArgumentTypeError("Bounding box coordinates are invalid.")
    return west, south, east, north


def parse_date(value: str, field: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError(f"{field} must use YYYY-MM-DD format: {value}") from error


def scene_time_utc(value: str) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    if value.endswith("Z") and timestamp.utcoffset() == timedelta(0):
        return value
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_access_token(session: requests.Session) -> str:
    username = os.environ.get("COPERNICUS_USERNAME", "").strip()
    password = os.environ.get("COPERNICUS_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD in varunetra-pipeline/.env."
        )

    response = session.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": "cdse-public",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"CDSE authentication failed with HTTP {response.status_code}.")
    try:
        token = response.json()["access_token"]
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError("CDSE authentication response did not contain an access token.") from error
    return token


def search_latest_product(
    session: requests.Session,
    token: str,
    bbox: tuple[float, float, float, float],
    start_date: datetime,
    end_date: datetime,
) -> dict:
    west, south, east, north = bbox
    polygon = (
        f"{west} {south},{east} {south},{east} {north},"
        f"{west} {north},{west} {south}"
    )
    end_exclusive = end_date + timedelta(days=1)
    filters = [
        "Collection/Name eq 'SENTINEL-1'",
        (
            "Attributes/OData.CSC.StringAttribute/"
            "any(att:att/Name eq 'productType' and "
            "att/OData.CSC.StringAttribute/Value eq 'IW_GRDH_1S')"
        ),
        (
            "OData.CSC.Intersects(area=geography'SRID=4326;"
            f"POLYGON(({polygon}))')"
        ),
        f"ContentDate/Start ge {start_date.strftime('%Y-%m-%d')}T00:00:00.000Z",
        f"ContentDate/Start lt {end_exclusive.strftime('%Y-%m-%d')}T00:00:00.000Z",
    ]
    params = {
            "$filter": " and ".join(filters),
            "$orderby": "ContentDate/Start desc",
            "$top": 100,
            "$select": "Id,Name,ContentDate,ContentLength",
    }
    url = CATALOGUE_URL
    headers = {"Authorization": f"Bearer {token}"}
    while url:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        try:
            page = response.json()
            products = page.get("value", [])
        except (ValueError, AttributeError) as error:
            raise RuntimeError("CDSE catalogue returned an invalid response.") from error

        for product in products:
            if re.search(
                r"S1[ABC]_IW_GRDH_1S(?:DV|SH)_",
                product.get("Name", ""),
                re.IGNORECASE,
            ):
                return product

        url = page.get("@odata.nextLink")
        params = None

    raise RuntimeError("No matching Sentinel-1 IW GRD products found for this target and date range.")


def download_product(session: requests.Session, token: str, product: dict) -> Path:
    product_name = product.get("Name", "")
    product_id = product.get("Id", "")
    if not product_name or not product_id:
        raise RuntimeError("CDSE product is missing its name or identifier.")

    downloads_dir = PIPELINE_DIR / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    destination = downloads_dir / f"{product_name}.zip"
    if destination.exists() and zipfile.is_zipfile(destination):
        return destination.resolve()

    partial_path = destination.with_suffix(".zip.part")
    partial_path.unlink(missing_ok=True)
    try:
        with session.get(
            f"{DOWNLOAD_URL}({product_id})/$value",
            headers={"Authorization": f"Bearer {token}"},
            stream=True,
            timeout=(REQUEST_TIMEOUT, 300),
        ) as response:
            response.raise_for_status()
            with partial_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        file.write(chunk)
        if not zipfile.is_zipfile(partial_path):
            raise RuntimeError("Downloaded response is not a valid ZIP archive.")
        partial_path.replace(destination)
    except (OSError, requests.RequestException, RuntimeError):
        partial_path.unlink(missing_ok=True)
        raise
    return destination.resolve()


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Search and download the newest Sentinel-1 IW GRD scene intersecting a bbox."
    )
    parser.add_argument("--bbox", required=True, type=parse_bbox, help="west,south,east,north")
    parser.add_argument("--start-date", required=True, help="Inclusive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Inclusive date, YYYY-MM-DD.")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        start_date = parse_date(args.start_date, "start-date")
        end_date = parse_date(args.end_date, "end-date")
        if end_date < start_date:
            raise ValueError("end-date must not be earlier than start-date.")

        load_dotenv(PIPELINE_DIR / ".env")
        with requests.Session() as session:
            token = get_access_token(session)
            product = search_latest_product(
                session, token, args.bbox, start_date, end_date
            )
            zip_path = download_product(session, token, product)

        result = {
            "zip_path": str(zip_path),
            "scene_time": scene_time_utc(product["ContentDate"]["Start"]),
            "product_name": product["Name"],
        }
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, requests.RequestException, RuntimeError, KeyError) as error:
        logger.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
