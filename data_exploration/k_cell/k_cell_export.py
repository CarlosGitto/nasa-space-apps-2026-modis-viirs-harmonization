"""Read-only export from the lake of what the per-cell k research needs (run with the etl venv).
Usage: etl/.venv/bin/python scripts/k_cell_export.py <out_dir>"""
import sys, os
sys.path.insert(0,'/home/gonzalo/devel/nasu/src/nasa-space-apps-2026-modis-viirs-harmonization/etl')
os.environ.setdefault('FIRMS_LAKE_PATH','/home/gonzalo/devel/nasu/src/nasa-space-apps-2026-modis-viirs-harmonization/data/lake')
import duckdb
from include.firms.config import attach_sql
out=sys.argv[1]; c=duckdb.connect(); c.execute(attach_sql(read_only=True)); c.execute("set memory_limit='6GB'")
c.execute(f"""copy (select latitude,longitude,acq_date::varchar acq_date,acq_time::int acq_time,frp,scan,track,satellite,daynight
 from lake.silver.int_detections_sp where sensor='modis' and try_cast(confidence_raw as int)>=30 and frp is not null) to '{out}/modis_raw.parquet'""")
c.execute(f"""copy (select acq_date::varchar acq_date,frp,daynight,h3_r6::varchar h3_id from lake.silver.int_detections_sp
 where satellite_code='N' and confidence_level<>'low' and frp is not null) to '{out}/snpp.parquet'""")
c.execute(f"""copy (select area, west, south, east, north, acq_month, modis_complete, viirs_complete
 from lake.gold.fct_area_coverage_monthly where west is not null) to '{out}/coverage.parquet'""")
print({f:c.execute(f"select count(*) from '{out}/{f}.parquet'").fetchone()[0] for f in ('modis_raw','snpp','coverage')})
