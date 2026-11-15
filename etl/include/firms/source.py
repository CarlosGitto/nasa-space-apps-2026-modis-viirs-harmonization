"""dlt sources for NASA FIRMS active fire detections (Area API, CSV).

Docs: https://firms.modaps.eosdis.nasa.gov/api/area/
URL:  /api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAY_RANGE}/{DATE}
      returns DATE .. DATE + DAY_RANGE - 1, with DAY_RANGE in 1..5.

Two sources, one per product family, so each keeps its own dlt schema/state:
- firms_nrt: near-real-time products (last ~2 months, updated continuously).
- firms_sp:  standard (science-quality) products, published with a lag of months.
Both load into the same dataset; each product gets its own table (e.g. `modis_sp`).

Resources are append-only: every extraction is kept as-is (versioned history);
deduplication happens downstream in dbt silver. Each row carries lineage
columns (`_firms_*`, `_extracted_at`) on top of dlt's `_dlt_load_id`.
"""

import csv
import io
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import dlt
from dlt.sources.helpers import requests

logger = logging.getLogger(__name__)

FIRMS_AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
MAX_DAYS_PER_REQUEST = 5

NRT_PRODUCTS = ("MODIS_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
SP_PRODUCTS = ("MODIS_SP", "VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "VIIRS_NOAA21_SP")

FLOAT_COLUMNS = {
    "latitude",
    "longitude",
    "brightness",
    "bright_t31",
    "bright_ti4",
    "bright_ti5",
    "scan",
    "track",
    "frp",
}

# A detection is uniquely identified by where, when and which satellite saw it.
PRIMARY_KEY = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]

# product -> (first day, last day), both inclusive
DateRanges = dict[str, tuple[date, date]]


def _parse_row(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if value == "":
            out[key] = None
        elif key in FLOAT_COLUMNS:
            out[key] = float(value)
        elif key == "acq_date":
            out[key] = date.fromisoformat(value)
        else:
            # acq_time stays text ("0012") to keep leading zeros;
            # confidence is numeric for MODIS but l/n/h for VIIRS.
            out[key] = value
    return out


def date_chunks(start: date, end: date, size: int = MAX_DAYS_PER_REQUEST) -> Iterator[tuple[date, int]]:
    """Split [start, end] into (chunk_start, n_days) pieces of at most `size` days."""
    current = start
    while current <= end:
        days = min(size, (end - current).days + 1)
        yield current, days
        current += timedelta(days=days)


# The MAP_KEY allows a number of transactions per 10-minute window, and large requests
# (the whole world) use many: past it FIRMS answers "Exceeding allowed transaction limit".
# Waiting for the window to roll over and retrying that one request beats failing the
# task, whose retry would repeat every request of the chunk and use even more of it.
TRANSACTION_LIMIT_WAIT_S = 120
TRANSACTION_LIMIT_RETRIES = 10  # up to 20 minutes: two full windows


def fetch_firms_csv(
    map_key: str, product: str, area: str, day_range: int, start_date: date
) -> list[dict]:
    import time

    url = f"{FIRMS_AREA_URL}/{map_key}/{product}/{area}/{day_range}/{start_date.isoformat()}"
    for attempt in range(TRANSACTION_LIMIT_RETRIES + 1):
        try:
            response = requests.get(url, timeout=300)
            response.raise_for_status()
            break
        except requests.HTTPError as e:
            # Re-raise without the URL so the MAP_KEY doesn't end up in task logs.
            status = e.response.status_code if e.response is not None else "?"
            body = e.response.text.strip()[:300] if e.response is not None else ""
            if "transaction limit" in body.lower() and attempt < TRANSACTION_LIMIT_RETRIES:
                logger.warning("FIRMS transaction limit reached (%s %s +%sd); waiting %ss (%s/%s)",
                               product, start_date, day_range, TRANSACTION_LIMIT_WAIT_S, attempt + 1,
                               TRANSACTION_LIMIT_RETRIES)
                time.sleep(TRANSACTION_LIMIT_WAIT_S)
                continue
            raise RuntimeError(f"FIRMS HTTP {status} for {product}/{area}: {body}") from None
    text = response.text
    # Some FIRMS errors come back as HTTP 200 with a plain-text message.
    if not text.startswith("latitude"):
        raise ValueError(f"FIRMS error for {product}/{area}: {text.strip()[:300]}")
    return [_parse_row(row) for row in csv.DictReader(io.StringIO(text))]


def fetch_availability(map_key: str) -> dict[str, tuple[date, date]]:
    """product -> (min_date, max_date) from the FIRMS data_availability endpoint.

    Returns {} if the response can't be parsed, so callers can fall back to
    not clamping their date ranges.
    """
    url = f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{map_key}/ALL"
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        out = {}
        for row in csv.DictReader(io.StringIO(response.text)):
            out[row["data_id"].strip()] = (
                date.fromisoformat(row["min_date"].strip()),
                date.fromisoformat(row["max_date"].strip()),
            )
        return out
    except Exception as e:  # noqa: BLE001 - availability is best-effort
        logger.warning("Could not read FIRMS data availability (%s); not clamping ranges", type(e).__name__)
        return {}


def _product_resources(map_key: str, ranges: DateRanges, area: str):
    def make_resource(product: str, start: date, end: date):
        @dlt.resource(
            name=product.lower(),
            write_disposition="append",
            primary_key=PRIMARY_KEY,  # hint only; dedup happens in dbt silver
        )
        def detections():
            for chunk_start, days in date_chunks(start, end):
                rows = fetch_firms_csv(map_key, product, area, days, chunk_start)
                logger.info("%s %s +%sd: %s rows", product, chunk_start, days, len(rows))
                lineage = {
                    "_firms_product": product,
                    "_firms_area": area,
                    "_firms_request_start": chunk_start,
                    "_firms_request_days": days,
                    "_firms_url": f"{FIRMS_AREA_URL}/***/{product}/{area}/{days}/{chunk_start.isoformat()}",
                    "_extracted_at": datetime.now(UTC),
                }
                if rows:
                    yield [row | lineage for row in rows]

        return detections

    return [make_resource(p, start, end) for p, (start, end) in ranges.items()]


@dlt.source(name="firms_nrt")
def firms_nrt_source(map_key: str, ranges: DateRanges, area: str = "world"):
    """area: "world" or "west,south,east,north" bbox."""
    return _product_resources(map_key, ranges, area)


@dlt.source(name="firms_sp")
def firms_sp_source(map_key: str, ranges: DateRanges, area: str = "world"):
    """area: "world" or "west,south,east,north" bbox."""
    return _product_resources(map_key, ranges, area)
