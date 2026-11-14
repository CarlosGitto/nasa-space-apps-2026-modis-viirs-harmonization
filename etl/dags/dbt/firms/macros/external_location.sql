{#
  Where `materialized: external` models are written. Overrides dbt-duckdb's
  default (<external_root>/<identifier>.parquet) so each layer gets its own
  folder in the lake, next to the raw data dlt lands, one folder per table:

    {FIRMS_LAKE_PATH}/firms_raw/<table>/...    (bronze, dlt)
    {FIRMS_LAKE_PATH}/silver/<table>/          (dbt)
    {FIRMS_LAKE_PATH}/gold/<table>/            (dbt)

  The table folder is created by DuckDB; the layer folder must already exist
  on local disk (Airflow creates it, see include/firms/config.py).
#}
{%- macro external_location(relation, config) -%}
  {{- env_var('FIRMS_LAKE_PATH', '/usr/local/airflow/data/lake') }}/{{ relation.schema }}/{{ relation.identifier }}
{%- endmacro -%}
