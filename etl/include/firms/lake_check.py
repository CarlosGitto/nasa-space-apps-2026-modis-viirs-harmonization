"""Check a DuckLake: what its catalog says against what is on disk. Read-only. Standalone (needs only `duckdb`).

    python lake_check.py --lake /path/to/lake                 # report
    python lake_check.py --lake /path/to/lake --json me.json  # also save it
    python lake_check.py --lake /path/to/lake --compare me.json   # differences against another machine's report

Run it on both machines (after a copy, an rsync or a rebuild) and compare:
- catalog problems (integrity, data path, snapshot),
- files the catalog needs that are missing or of another size (a copy that did not finish, or the catalog and the files
  coming from different moments),
- per table: rows and date range, from the data itself,
- files on disk the catalog no longer uses (old versions: what the weekly maintenance DAG frees).
A process that already had the catalog open before it was replaced keeps reading the OLD one (rsync swaps the file):
restart the backend / Airflow after copying a lake.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import duckdb

DATE_COLUMNS = ("acq_date", "acq_month", "obs_date", "obs_month")


def catalog_report(lake: Path) -> dict:
    con = sqlite3.connect(f"file:{lake / 'catalog.sqlite'}?mode=ro", uri=True)
    meta = dict(con.execute("select key, value from ducklake_metadata").fetchall())
    snap = con.execute("select snapshot_id, snapshot_time from ducklake_snapshot order by snapshot_id desc limit 1").fetchone()
    check = con.execute("pragma quick_check").fetchone()[0]
    rows = con.execute(
        """
        select s.schema_name, t.table_name, f.path, f.path_is_relative, s.path, s.path_is_relative, t.path, t.path_is_relative,
               f.file_size_bytes
        from ducklake_data_file f
        join ducklake_table t on t.table_id = f.table_id and t.end_snapshot is null
        join ducklake_schema s on s.schema_id = t.schema_id and s.end_snapshot is null
        where f.end_snapshot is null
        """
    ).fetchall()
    every = {r[0] for r in con.execute("select path from ducklake_data_file")}
    con.close()
    base = lake / "files"
    expected, missing, wrong_size = {}, [], []
    for schema, table, fpath, frel, spath, srel, tpath, trel, size in rows:
        path = base
        if srel:
            path = path / spath
        else:
            path = Path(spath)
        path = path / tpath if trel else Path(tpath)
        path = path / fpath if frel else Path(fpath)
        key = f"{schema}.{table}"
        expected[key] = expected.get(key, 0) + 1
        if not path.exists():
            missing.append(str(path.relative_to(base)) if base in path.parents else str(path))
        elif size is not None and path.stat().st_size != size:
            wrong_size.append(str(path.relative_to(base)))
    on_disk = [p for p in base.rglob("*.parquet")] if base.exists() else []
    stale = [p for p in on_disk if p.name not in {Path(x).name for x in every}]
    return {
        "integrity": check, "data_path": meta.get("data_path"), "snapshot": snap[0] if snap else None,
        "snapshot_time": str(snap[1]) if snap else None, "files_needed": sum(expected.values()), "files_per_table": expected,
        "missing_files": len(missing), "missing_examples": missing[:8], "wrong_size_files": len(wrong_size),
        "wrong_size_examples": wrong_size[:8], "parquet_on_disk": len(on_disk),
        "unreferenced_files": len(stale), "unreferenced_mb": round(sum(p.stat().st_size for p in stale) / 1e6),
    }


def table_report(lake: Path) -> dict:
    con = duckdb.connect()
    con.execute("install ducklake; load ducklake; install sqlite; load sqlite")
    con.execute(f"attach 'ducklake:sqlite:{lake / 'catalog.sqlite'}' as lake "
                f"(data_path '{lake / 'files'}/', override_data_path true, read_only)")
    out = {}
    tables = con.execute("select table_schema, table_name from information_schema.tables where table_catalog = 'lake' "
                         "and table_schema <> 'main' and table_type = 'BASE TABLE' order by 1, 2").fetchall()
    for schema, table in tables:
        name, entry = f"{schema}.{table}", {}
        try:
            cols = {r[0] for r in con.execute("select column_name from information_schema.columns where table_catalog = 'lake' "
                                              "and table_schema = ? and table_name = ?", [schema, table]).fetchall()}
            date_col = next((c for c in DATE_COLUMNS if c in cols), None)
            q = f"select count(*)" + (f", min({date_col}), max({date_col})" if date_col else "") + f" from lake.{schema}.\"{table}\""
            row = con.execute(q).fetchone()
            entry = {"rows": row[0], "first": str(row[1]) if date_col else None, "last": str(row[2]) if date_col else None}
        except Exception as e:  # a missing file shows up here as an IO error
            entry = {"error": str(e).splitlines()[0][:160]}
        out[name] = entry
    return out


def show(report: dict) -> None:
    c = report["catalog"]
    print(f"catalog: integrity {c['integrity']} · snapshot {c['snapshot']} ({c['snapshot_time']}) · data_path {c['data_path']}")
    print(f"files: {c['files_needed']} needed, {c['missing_files']} missing, {c['wrong_size_files']} of another size · "
          f"{c['parquet_on_disk']} parquet on disk, {c['unreferenced_files']} unreferenced ({c['unreferenced_mb']} MB, old versions)")
    for key in ("missing_examples", "wrong_size_examples"):
        for x in c[key]:
            print(f"  ! {key.split('_')[0]}: {x}")
    print(f"\n{'table':46s} {'rows':>12s}  first -> last")
    for name, t in report["tables"].items():
        if "error" in t:
            print(f"{name:46s} {'ERROR':>12s}  {t['error']}")
        else:
            print(f"{name:46s} {t['rows']:>12,}  {t['first'] or ''} -> {t['last'] or ''}")


def compare(mine: dict, other: dict) -> int:
    print("\nDifferences against the other report (mine vs other):")
    n = 0
    for key in ("integrity", "data_path", "snapshot"):
        a, b = mine["catalog"][key], other["catalog"][key]
        if a != b:
            print(f"  catalog {key}: {a} vs {b}")
            n += 1
    names = sorted(set(mine["tables"]) | set(other["tables"]))
    for name in names:
        a, b = mine["tables"].get(name), other["tables"].get(name)
        if a is None or b is None:
            print(f"  {name}: only in {'mine' if b is None else 'the other'}")
            n += 1
        elif a != b:
            print(f"  {name}: {a.get('rows')} rows {a.get('first')}..{a.get('last')} vs {b.get('rows')} rows {b.get('first')}..{b.get('last')}")
            n += 1
    print("  none: same tables, rows and date ranges" if not n else f"  {n} difference(s)")
    return n


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--lake", type=Path, required=True, help="lake folder (the one with catalog.sqlite and files/)")
    p.add_argument("--json", type=Path, help="save the report here")
    p.add_argument("--compare", type=Path, help="report of another machine to compare with")
    args = p.parse_args()
    lake = args.lake.resolve()
    if not (lake / "catalog.sqlite").exists():
        print(f"no catalog.sqlite in {lake}", file=sys.stderr)
        return 2
    report = {"lake": str(lake), "catalog": catalog_report(lake), "tables": table_report(lake)}
    show(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
    bad = report["catalog"]["missing_files"] + report["catalog"]["wrong_size_files"]
    if args.compare:
        bad += compare(report, json.loads(args.compare.read_text()))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
