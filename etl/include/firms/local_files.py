"""Read FIRMS CSV files that were downloaded outside Airflow and turn them into Parquet
shaped exactly like the bronze tables dlt writes (`lake.firms_raw.<product>`).

Pure steps, no Airflow and no lake needed (DuckDB + PyArrow only):

    discover()          find the files, work out their product, validate their header
    stage_to_parquet()  one Parquet per product: typed, de-duplicated, with lineage columns

`load_local.py` takes that Parquet and appends it to the lake with dlt, so silver and
gold (dbt) run on it unchanged.

What counts as a FIRMS file: a CSV from the FIRMS Area API (the same columns as
`source.py` parses) whose name contains the product, e.g. `MODIS_SP`, `VIIRS_NOAA20_SP`,
`VIIRS_SNPP_NRT`. The product decides the bronze table (`viirs_noaa20_sp`, ...).
Everything else in the folder is skipped and reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa

PRODUCT_RE = re.compile(r"(MODIS_(?:SP|NRT)|VIIRS_(?:SNPP|NOAA20|NOAA21)_(?:SP|NRT))", re.IGNORECASE)
RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Columns of a FIRMS Area API CSV, in order (see source.py). SP files add `type`.
_MODIS_COLUMNS = ["latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time", "satellite",
                  "instrument", "confidence", "version", "bright_t31", "frp", "daynight"]
_VIIRS_COLUMNS = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time", "satellite",
                  "instrument", "confidence", "version", "bright_ti5", "frp", "daynight"]
FLOAT_COLUMNS = {"latitude", "longitude", "brightness", "bright_t31", "bright_ti4", "bright_ti5", "scan", "track", "frp"}

# A detection is uniquely identified by where, when and which satellite saw it (source.py).
PRIMARY_KEY = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]

LINEAGE_COLUMNS = ["_firms_product", "_firms_area", "_firms_request_start", "_firms_request_days", "_firms_url",
                   "_extracted_at"]


def columns_for(product: str) -> list[str]:
    """Bronze data columns of a product, in FIRMS order (SP carries `type`)."""
    base = _MODIS_COLUMNS if product.upper().startswith("MODIS") else _VIIRS_COLUMNS
    return base + (["type"] if product.upper().endswith("_SP") else [])


@dataclass
class LocalFile:
    path: Path
    product: str  # e.g. VIIRS_NOAA20_SP
    prefix: str  # what comes before the product in the file name (a label of the area), or the folder name
    size: int
    mtime_ns: int
    name_start: date | None = None  # first day the file name announces (None if it has no date)
    name_end: date | None = None
    has_type: bool = False  # the CSV header has the `type` column
    empty: bool = False  # header only: the window was downloaded and had no detections

    @property
    def table(self) -> str:
        return self.product.lower()

    @property
    def family(self) -> str:
        return "sp" if self.product.endswith("_SP") else "nrt"


@dataclass
class Skipped:
    path: Path
    reason: str


def product_of(path: Path) -> str | None:
    """FIRMS product named in the file name (upper case), or None."""
    m = PRODUCT_RE.search(path.name)
    return m.group(1).upper() if m else None


def prefix_of(path: Path, product: str) -> str:
    """Label in front of the product in the file name ("AR_north_MODIS_SP_2015-01-01.csv" -> "AR_north").
    Without one, the name of the folder."""
    before = path.name[: PRODUCT_RE.search(path.name).start()].strip("_- ")
    return before or path.parent.name


def dates_of(path: Path) -> tuple[date | None, date | None]:
    """(start, end) days written in the file name: `X_2020-09-10_to_2020-09-19.csv`, or `X_2012-01-19.csv`
    (start only). (None, None) when it has no date."""
    m = RANGE_RE.search(path.name)
    if m:
        return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    m = DATE_RE.search(path.name)
    return (date.fromisoformat(m.group(1)), None) if m else (None, None)


def read_header(path: Path) -> list[str] | None:
    try:
        with path.open(encoding="utf-8") as f:
            line = f.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return [c.strip() for c in line.split(",")] if line else None


def discover(
    root: Path,
    products: set[str] | None = None,
    prefixes: set[str] | None = None,
    include: str = "*.csv",
    exclude: tuple[str, ...] = (),
    since: date | None = None,
    until: date | None = None,
    window_days: int | None = None,
    with_empty: bool = False,
) -> tuple[list[LocalFile], list[Skipped]]:
    """Every valid FIRMS CSV under `root` (recursive), with the reasons for the rest.

    products   keep only these (e.g. {"MODIS_SP"}); case-insensitive
    prefixes   keep only files whose area label is one of these
    since/until  keep files whose name-announced days overlap [since, until] (files without a date in the name always stay)
    window_days  days a file named with only a start date covers (the downloads were cut in windows of N days, the
                 last one of the year shorter): fills the end of the window, which the CSV itself cannot tell when
                 the days without detections are missing. Without it such a file is taken to cover the days it has data.
    with_empty   keep header-only files (flagged `empty`) instead of skipping them: they are downloaded windows with
                 no detections, which matter to know what was covered
    """
    products = {p.upper() for p in products} if products else None
    files: list[LocalFile] = []
    skipped: list[Skipped] = []
    for path in sorted(Path(root).rglob(include)):
        if not path.is_file():
            continue
        if any(path.match(pat) for pat in exclude):
            skipped.append(Skipped(path, "excluded"))
            continue
        product = product_of(path)
        if product is None:
            skipped.append(Skipped(path, "no FIRMS product in the file name"))
            continue
        if products and product not in products:
            skipped.append(Skipped(path, "product not selected"))
            continue
        prefix = prefix_of(path, product)
        if prefixes and prefix not in prefixes:
            skipped.append(Skipped(path, "area label not selected"))
            continue
        header = read_header(path)
        expected = columns_for(product)
        if header is None or header not in (expected, expected[:-1] if expected[-1] == "type" else expected):
            skipped.append(Skipped(path, "header is not a FIRMS CSV header" if header else "empty or unreadable"))
            continue
        stat = path.stat()
        # A file with only the header has nothing to load.
        empty = stat.st_size <= len(",".join(header)) + 2
        if empty and not with_empty:
            skipped.append(Skipped(path, "no rows"))
            continue
        start, end = dates_of(path)
        if start and not end and window_days:
            end = min(start + timedelta(days=window_days - 1), date(start.year, 12, 31))
        if (since or until) and start:
            last = end or start
            if (until and start > until) or (since and last < since):
                skipped.append(Skipped(path, "outside the date range"))
                continue
        files.append(LocalFile(path, product, prefix, stat.st_size, stat.st_mtime_ns, start, end, "type" in header, empty))
    return files, skipped


@dataclass
class Staged:
    table: str
    product: str
    parquet: Path
    files: list[LocalFile] = field(default_factory=list)
    rows_read: int = 0
    rows_loaded: int = 0  # after dropping detections repeated across files
    first_day: date | None = None
    last_day: date | None = None


def area_label(prefix: str, areas: dict[str, str] | None) -> str:
    """Value of `_firms_area`: the bbox "west,south,east,north" when the prefix is known, else `local:<prefix>`."""
    return (areas or {}).get(prefix) or f"local:{prefix}"


def stage_to_parquet(
    files: list[LocalFile],
    out_dir: Path,
    areas: dict[str, str] | None = None,
    root: Path | None = None,
    memory_limit: str = "4GB",
) -> list[Staged]:
    """One Parquet per product in `out_dir`, with the schema of the bronze tables.

    - types as in source.py (floats, `acq_date` as date, everything else text, empty = NULL)
    - the same detection found in several files (overlapping areas or windows) is kept once
    - lineage columns: `_firms_product`, `_firms_area` (bbox or `local:<prefix>`), `_firms_request_start`
      and `_firms_request_days` (from the file name, else from the dates inside), `_firms_url`
      (`local-file://<path relative to root>`: no keys, no absolute paths), `_extracted_at` (file mtime)
    Row groups are small so the next step streams it in batches. `memory_limit` caps DuckDB (it spills to disk).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_product: dict[str, list[LocalFile]] = {}
    for f in files:
        by_product.setdefault(f.product, []).append(f)

    staged: list[Staged] = []
    with duckdb.connect() as con:
        # De-duplicating tens of millions of rows is the heavy step: cap DuckDB's memory (it spills to out_dir).
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET temp_directory = '{out_dir / 'duckdb_tmp'}'")
        for product, group in sorted(by_product.items()):
            cols = columns_for(product)
            paths = [str(f.path) for f in group]
            meta = pa.table({
                "filename": paths,
                "area": [area_label(f.prefix, areas) for f in group],
                "req_start": pa.array([f.name_start for f in group], type=pa.date32()),
                "req_end": pa.array([f.name_end for f in group], type=pa.date32()),
                "extracted_at": pa.array([datetime.fromtimestamp(f.mtime_ns / 1e9, UTC) for f in group],
                                         type=pa.timestamp("us", tz="UTC")),
                "url": [f"local-file://{_relative(f.path, root)}" for f in group],
            })
            con.register("meta", meta)
            typed_cols = ", ".join(
                f"try_cast({c} AS DOUBLE) AS {c}" if c in FLOAT_COLUMNS
                else f"try_cast({c} AS DATE) AS {c}" if c == "acq_date"
                else f"nullif(trim({c}), '') AS {c}"
                for c in cols if c != "type"
            )
            typed_cols += ", nullif(trim(type), '') AS type" if "type" in cols else ""
            has_type = any(f.has_type for f in group)
            if "type" in cols and not has_type:  # SP rows from a CSV without `type`: keep the column, empty
                typed_cols = typed_cols.replace(", nullif(trim(type), '') AS type", ", NULL::VARCHAR AS type")
            # Headers were validated in discover(); unifying schemas file by file is slow with thousands of files,
            # so only do it when files with and without `type` are mixed.
            union = "true" if len({f.has_type for f in group}) > 1 else "false"
            read_csv = (f"read_csv(?, header = true, all_varchar = true, filename = true, union_by_name = {union}, "
                        "ignore_errors = false, parallel = true)")
            key = ", ".join(PRIMARY_KEY)
            query = f"""
                WITH typed AS MATERIALIZED (
                    SELECT {typed_cols}, filename FROM {read_csv}
                ),
                span AS (SELECT filename, min(acq_date) AS d0, max(acq_date) AS d1 FROM typed GROUP BY filename),
                dedup AS (
                    SELECT * FROM typed
                    QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY filename) = 1
                )
                SELECT {", ".join(f"d.{c}" for c in cols)},
                       '{product}' AS _firms_product,
                       m.area AS _firms_area,
                       coalesce(m.req_start, s.d0) AS _firms_request_start,
                       (date_diff('day', coalesce(m.req_start, s.d0), coalesce(m.req_end, s.d1)) + 1)::BIGINT
                           AS _firms_request_days,
                       m.url AS _firms_url,
                       m.extracted_at AS _extracted_at
                FROM dedup d
                JOIN meta m USING (filename)
                JOIN span s USING (filename)
            """
            target = out_dir / f"{product.lower()}.parquet"
            rows_read = con.execute(f"SELECT count(*) FROM {read_csv}", [paths]).fetchone()[0]
            con.execute(f"COPY ({query}) TO '{target}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 250000)",
                        [paths])
            n, d0, d1 = con.execute(f"SELECT count(*), min(acq_date), max(acq_date) FROM read_parquet('{target}')").fetchone()
            staged.append(Staged(product.lower(), product, target, group, rows_read, n, d0, d1))
            con.unregister("meta")
    return staged


def _relative(path: Path, root: Path | None) -> str:
    try:
        return str(path.resolve().relative_to(Path(root).resolve())) if root else path.name
    except ValueError:
        return path.name
