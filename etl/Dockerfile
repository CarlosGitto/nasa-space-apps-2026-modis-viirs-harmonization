FROM astrocrpublic.azurecr.io/runtime:3.3-12
# Astro Runtime includes the following pre-installed providers packages: https://www.astronomer.io/docs/astro/runtime-image-architecture#provider-packages

# dbt lives in its own venv so its dependencies don't clash with Airflow's.
# duckdb is pinned to the same version dlt uses, so both read/write the same file format.
RUN python -m venv dbt_venv && \
    dbt_venv/bin/pip install --no-cache-dir dbt-core==1.12.6 dbt-duckdb==1.11.1 duckdb==1.5.6

# DuckLake + SQLite catalog + H3 grid extensions, preinstalled so tasks don't download
# them at runtime. Both venvs use DuckDB 1.5.6 and share ~/.duckdb/extensions.
RUN python -c "import duckdb; duckdb.execute('INSTALL ducklake; INSTALL sqlite; INSTALL h3 FROM community')"
