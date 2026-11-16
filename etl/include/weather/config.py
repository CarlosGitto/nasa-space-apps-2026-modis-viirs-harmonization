"""Settings shared by the weather ingest and dbt DAGs.

Weather (NASA POWER, see power.py) lands in the same DuckLake as FIRMS (see
include/firms/config.py), in its own bronze schema `lake.weather_raw`, and goes
through the same single-writer pool. No credentials: POWER needs no login.

Region and dates follow the FIRMS dev dataset on purpose: weather is only
useful where and when there are fire detections to explain. If you change one,
change the other (see "Changing the region or the dates" in data/README.md):

- area:  WEATHER_AREA in etl/.env, which defaults to FIRMS_AREA.
- dates: WEATHER_DEFAULT_START here (same as SP_DEFAULT_START in
         dags/firms_ingest_sp.py) and DEV_START in the Makefile.
"""

import os
from datetime import date

from airflow.sdk import Asset

from include.firms.config import FIRMS_AREA

WEATHER_RAW_DATASET = "weather_raw"

# Area requested from POWER: "world" or "west,south,east,north" (the FIRMS format).
WEATHER_AREA = os.getenv("WEATHER_AREA", FIRMS_AREA)

# First day loaded when a product has nothing in the lake yet. Same as FIRMS SP.
WEATHER_DEFAULT_START = date(2025, 1, 1)

# Updated by weather_ingest, consumed by weather_dbt.
WEATHER_RAW = Asset("weather_raw")
