"""Load FIRMS CSV files you already have into the bronze tables of the lake.

    cd etl && uv run python -m include.firms.load_local --root /path/to/csvs [options]
    make etl-load-local DIR=/path/to/csvs ARGS="--dry-run"          # same, from the repo root

The files are the CSVs the FIRMS Area API returns (one per request), in any folder layout; the
product in the file name (`MODIS_SP`, `VIIRS_NOAA20_SP`, ...) says which bronze table each one
belongs to. They are staged as Parquet (typed, repeated detections dropped, lineage columns, see
`local_files.py`) and appended with the same dlt pipeline the Airflow ingest uses, so the rows are
indistinguishable from API loads: silver and gold (dbt) take them like any other load and keep the
latest version of each detection.

It does NOT run dbt. After loading, rebuild silver and gold from the raw data (Airflow running):

    make etl-reprocess

Files already loaded (same path, size and modification time) are skipped; the list is kept in the lake
(`firms_raw.local_files_loaded`), so it travels with it. `--force` loads them again.

Silver keeps the row of the latest load for each detection (not of the newest data): a file loaded after an API
load replaces the API row of the same detection. Load old files first, or only what the lake doesn't have.

Use `--dry-run` first: it lists what it found and what it would load, and touches nothing.
Airflow must not be writing to the lake at the same time (the catalog accepts one writer).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from include.firms.local_files import PRIMARY_KEY, LocalFile, Staged, area_label, discover, stage_to_parquet

REPO_LAKE = Path(__file__).resolve().parents[3] / "data" / "lake"  # <repo>/data/lake
MANIFEST_TABLE = "local_files_loaded"
WINDOWS_TABLE = "download_windows"  # every window downloaded (also those with no detections), see register_windows


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def already_loaded(root: Path) -> set[tuple[str, int, int]]:
    """(relative path, size, mtime_ns) of the files this loader put in the lake before."""
    import duckdb

    from include.firms.config import LAKE_ALIAS, RAW_DATASET, attach_sql, catalog_initialized

    if not catalog_initialized():
        return set()
    with duckdb.connect() as con:
        con.execute(attach_sql(read_only=True))
        exists = con.execute(
            "select count(*) from information_schema.tables where table_catalog = ? and table_schema = ? "
            "and table_name = ?", [LAKE_ALIAS, RAW_DATASET, MANIFEST_TABLE]).fetchone()[0]
        if not exists:
            return set()
        rows = con.execute(f'select path, size, mtime_ns from {LAKE_ALIAS}.{RAW_DATASET}."{MANIFEST_TABLE}" '
                           "where root = ?", [str(root.name)]).fetchall()
    return {(p, int(s), int(m)) for p, s, m in rows}


def _rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def run_load(staged: list[Staged], root: Path, batch_rows: int) -> None:
    """Append the staged Parquet files to the bronze tables, one dlt pipeline per product family (sp / nrt)."""
    import dlt
    import pyarrow as pa
    import pyarrow.parquet as pq

    from include.firms.config import raw_pipeline

    # For Parquet (Arrow) data dlt only adds its own columns when asked. Silver needs `_dlt_load_id` (latest version
    # of each detection, incremental scope) and API loads carry `_dlt_id` too.
    os.environ.setdefault("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID", "true")
    os.environ.setdefault("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_ID", "true")
    # dlt reads Airflow Variables when Airflow is importable; run from a terminal (no Airflow database) that only
    # prints hundreds of harmless tracebacks. The loader needs no secrets.
    os.environ.setdefault("PROVIDERS__ENABLE_AIRFLOW_SECRETS", "false")

    def detections_resource(st: Staged):
        @dlt.resource(name=st.table, write_disposition="append", primary_key=PRIMARY_KEY)
        def detections():
            for batch in pq.ParquetFile(st.parquet).iter_batches(batch_size=batch_rows):
                yield pa.Table.from_batches([batch])

        return detections

    for family in ("sp", "nrt"):
        group = [s for s in staged if s.product.endswith("_" + family.upper())]
        if not group:
            continue

        loaded_at = datetime.now(UTC)
        manifest = [
            {"root": root.name, "path": _rel(f.path, root), "size": f.size, "mtime_ns": f.mtime_ns,
             "product": f.product, "loaded_at": loaded_at}
            for s in group for f in s.files
        ]

        @dlt.resource(name=MANIFEST_TABLE, write_disposition="append")
        def local_files_loaded():
            yield manifest

        @dlt.source(name=f"firms_{family}")
        def source():
            return [detections_resource(s) for s in group] + [local_files_loaded]

        pipeline = raw_pipeline(f"firms_{family}")
        print(f"\nLoading {sum(s.rows_loaded for s in group):,} rows into {', '.join(s.table for s in group)} ...")
        print(pipeline.run(source()))


def known_windows() -> set[tuple[str, str, date]]:
    """(product, area, start) of the windows already registered."""
    import duckdb

    from include.firms.config import LAKE_ALIAS, RAW_DATASET, attach_sql, catalog_initialized

    if not catalog_initialized():
        return set()
    with duckdb.connect() as con:
        con.execute(attach_sql(read_only=True))
        if not con.execute("select count(*) from information_schema.tables where table_catalog = ? and "
                           "table_schema = ? and table_name = ?", [LAKE_ALIAS, RAW_DATASET, WINDOWS_TABLE]).fetchone()[0]:
            return set()
        return {tuple(r) for r in con.execute(
            f'select product, area, start_date from {LAKE_ALIAS}.{RAW_DATASET}."{WINDOWS_TABLE}"').fetchall()}


def register_windows(files: list[LocalFile], areas: dict | None) -> int:
    """Record which area/product/days were downloaded (`firms_raw.download_windows`), empty windows included.

    The detections alone cannot say it: a window without fire leaves no row, and without knowing it was downloaded
    "no detection" cannot be told from "no data" (dbt fct_area_coverage_monthly reads this table).
    Needs the end of each window: files named with only a start date require --window-days.
    """
    import dlt

    from include.firms.config import raw_pipeline

    known = known_windows()
    rows, seen = [], set()
    for f in files:
        if not f.name_start:
            continue
        end = f.name_end or f.name_start
        area = area_label(f.prefix, areas)
        key = (f.product, area, f.name_start)
        if key in known or key in seen:
            continue
        seen.add(key)
        rows.append({"product": f.product, "area": area, "start_date": f.name_start, "days": (end - f.name_start).days + 1,
                     "with_detections": not f.empty, "registered_at": datetime.now(UTC)})
    if not rows:
        return 0

    @dlt.resource(name=WINDOWS_TABLE, write_disposition="append")
    def windows():
        yield rows

    os.environ.setdefault("PROVIDERS__ENABLE_AIRFLOW_SECRETS", "false")
    print(raw_pipeline("firms_windows").run(dlt.source(lambda: windows, name="firms_windows")()))
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", required=True, type=Path, help="folder with the CSV files (searched recursively)")
    p.add_argument("--lake", type=Path, help=f"lake folder (default: $FIRMS_LAKE_PATH or {REPO_LAKE})")
    p.add_argument("--product", action="append", help="only this product, e.g. MODIS_SP (repeatable)")
    p.add_argument("--prefix", action="append", help="only files whose area label (name prefix) is this (repeatable)")
    p.add_argument("--include", default="*.csv", help="file name pattern (default *.csv)")
    p.add_argument("--exclude", action="append", default=[], help="skip files matching this glob (repeatable)")
    p.add_argument("--since", type=date.fromisoformat, help="skip files whose name-announced days end before this date")
    p.add_argument("--until", type=date.fromisoformat, help="skip files whose name-announced days start after this date")
    p.add_argument("--window-days", type=int, help="days a file named with only a start date covers (capped at the end "
                   "of its year); needed to know what was downloaded when the empty days are missing")
    p.add_argument("--windows-only", action="store_true", help="only record which windows were downloaded "
                   "(firms_raw.download_windows), do not load detections")
    p.add_argument("--areas", type=Path, help='JSON {"<name prefix>": "west,south,east,north"} to fill _firms_area')
    p.add_argument("--staging", type=Path, help="where the intermediate Parquet goes (default: a temp folder)")
    p.add_argument("--keep-staging", action="store_true", help="do not delete the intermediate Parquet")
    p.add_argument("--batch-rows", type=int, default=250_000, help="rows per batch sent to dlt (default 250000)")
    p.add_argument("--memory-limit", default="4GB", help="memory DuckDB may use while staging (default 4GB; it spills to disk)")
    p.add_argument("--dry-run", action="store_true", help="list what would be loaded and stop")
    p.add_argument("--force", action="store_true", help="load files already loaded before")
    args = p.parse_args(argv)

    os.environ["FIRMS_LAKE_PATH"] = str((args.lake or Path(os.getenv("FIRMS_LAKE_PATH", REPO_LAKE))).resolve())
    os.umask(0)  # Airflow writes as another user (uid 50000): what we create there must stay writable for it
    root = args.root.resolve()
    if not root.is_dir():
        print(f"--root {root} is not a folder", file=sys.stderr)
        return 2
    areas = json.loads(args.areas.read_text()) if args.areas else None

    files, skipped = discover(root, set(args.product) if args.product else None,
                              set(args.prefix) if args.prefix else None, args.include, tuple(args.exclude),
                              args.since, args.until, args.window_days, with_empty=True)
    print(f"Lake: {os.environ['FIRMS_LAKE_PATH']}\nFolder: {root}")
    reasons = Counter(s.reason for s in skipped)
    if reasons:
        print("Skipped: " + "; ".join(f"{n} {r}" for r, n in reasons.most_common()))
        for reason in ("header is not a FIRMS CSV header", "empty or unreadable"):
            for s in [s for s in skipped if s.reason == reason][:5]:
                print(f"  ! {reason}: {s.path}")

    n_empty = sum(f.empty for f in files)
    if n_empty:
        print(f"{n_empty} files have only the header (windows with no detections): nothing to load, but they count as downloaded")
    if not args.dry_run:
        n = register_windows(files, areas)
        print(f"Download windows registered: {n} new (of {sum(1 for f in files if f.name_start)} with a date in the name)")
    if args.windows_only:
        return 0

    already: set = set() if args.force else already_loaded(root)
    todo: list[LocalFile] = [f for f in files if not f.empty and (_rel(f.path, root), f.size, f.mtime_ns) not in already]
    if len(todo) != len(files) - n_empty:
        print(f"Already in the lake: {len(files) - n_empty - len(todo)} files (use --force to load them again)")

    by_table = Counter(f.table for f in todo)
    print(f"\n{len(todo)} files to load:")
    for table in sorted(by_table):
        group = [f for f in todo if f.table == table]
        print(f"  {table:18s} {len(group):6d} files  {_fmt_bytes(sum(f.size for f in group)):>9}  "
              f"labels: {', '.join(sorted({f.prefix for f in group})[:8])}"
              + (" ..." if len({f.prefix for f in group}) > 8 else ""))
    if not todo:
        return 0
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    staging = args.staging or Path(tempfile.mkdtemp(prefix="firms_local_"))
    try:
        print(f"\nStaging to Parquet in {staging} ...")
        staged = stage_to_parquet(todo, staging, areas, root, args.memory_limit)
        for s in staged:
            print(f"  {s.table:18s} {s.rows_read:>12,} rows read -> {s.rows_loaded:>12,} detections "
                  f"({s.rows_read - s.rows_loaded:,} repeated across files)  {s.first_day} .. {s.last_day}")
        run_load(staged, root, args.batch_rows)
    finally:
        if not args.keep_staging and not args.staging:
            shutil.rmtree(staging, ignore_errors=True)
    print("\nDone. Silver and gold are not rebuilt yet: run `make etl-reprocess` (Airflow running).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
