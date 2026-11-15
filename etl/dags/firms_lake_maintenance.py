"""
## FIRMS lake maintenance (DuckLake)

Weekly housekeeping of the DuckLake catalog (`include/firms/config.py`):

1. **Flush inlined data**: small inserts are stored inside the catalog; write them to Parquet.
2. **Merge adjacent files**: compact the many small files left by daily loads and incremental runs.
3. **Expire snapshots** older than `keep_days`: that is the time-travel window.
4. **Clean up old files** no longer referenced by any remaining snapshot (frees disk).

Writes to the lake, so it takes the single-writer pool like the loads and dbt.
"""

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import LAKE_WRITER_POOL


@dag(
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    schedule="@weekly",
    catchup=False,
    max_active_runs=1,
    doc_md=__doc__,
    default_args={"owner": "etl", "retries": 1, "retry_delay": timedelta(minutes=10)},
    params={
        "keep_days": Param(7, type="integer", minimum=1, description="Days of snapshots (time travel) to keep"),
    },
    tags=["firms", "ducklake", "maintenance"],
)
def firms_lake_maintenance():
    @task(pool=LAKE_WRITER_POOL)
    def maintain_lake(params=None):
        import duckdb

        from include.firms.config import LAKE_ALIAS, attach_sql, catalog_initialized

        if not catalog_initialized():
            from airflow.sdk.exceptions import AirflowSkipException

            raise AirflowSkipException("No lake catalog yet")

        older_than = f"now() - interval {int(params['keep_days'])} day"
        steps = [
            f"CALL ducklake_flush_inlined_data('{LAKE_ALIAS}')",
            f"CALL ducklake_merge_adjacent_files('{LAKE_ALIAS}')",
            f"CALL ducklake_expire_snapshots('{LAKE_ALIAS}', older_than => {older_than})",
            f"CALL ducklake_cleanup_old_files('{LAKE_ALIAS}', older_than => {older_than})",
        ]
        with duckdb.connect() as con:
            con.execute(attach_sql())
            for sql in steps:
                result = con.execute(sql).fetchall()
                print(f"{sql}: {len(result)} rows affected")

    maintain_lake()


firms_lake_maintenance()
