{#
  Incremental scope shared by every incremental model: the months that received
  rows from loads newer than anything the model already has. Returns a SQL
  predicate on the source rows (a date column + a load id).

    source_sql        the model's source query (rows to filter)
    source_load_col   load id column in the source rows        (default _dlt_load_id)
    target_load_col   load id column already stored in {{ this }} (default _dlt_load_id)
    date_col          date column of the source rows           (default acq_date;
                      the weather models use obs_date)

  Recomputing whole months is correct because every key we dedup or group by
  includes that date: all versions of a detection (or of a grid cell's hour),
  and all rows of a day, fall in the same month. The month list and the watermark are rendered as literals (not
  subqueries) so DuckLake can skip files using its min/max column statistics.

  _dlt_load_id is compared as text: it is "<unix seconds>.<fraction>" with a
  10-digit integer part (until year 2286), so text order matches numeric order.
#}
{% macro incremental_months_scope(source_sql, source_load_col='_dlt_load_id', target_load_col='_dlt_load_id', date_col='acq_date') %}
    {% set watermark = run_query("select max(" ~ target_load_col ~ ") from " ~ this).columns[0].values()[0] %}
    {% if watermark is none %}
        {{ return("true") }}
    {% endif %}
    {% set months = run_query(
        "select distinct strftime(date_trunc('month', " ~ date_col ~ "), '%Y-%m-%d') from (" ~ source_sql ~ ") "
        ~ "where " ~ source_load_col ~ " > '" ~ watermark ~ "' and " ~ date_col ~ " is not null order by 1"
    ).columns[0].values() %}
    {% do log(this ~ ": watermark " ~ watermark ~ ", months to recompute: " ~ (months | join(", ") or "none"), info=true) %}
    {% set new_without_date = "(" ~ date_col ~ " is null and " ~ source_load_col ~ " > '" ~ watermark ~ "')" %}
    {% if months | length == 0 %}
        {{ return(new_without_date) }}
    {% endif %}
    {% set month_list = [] %}
    {% for m in months %}
        {% do month_list.append("date '" ~ m ~ "'") %}
    {% endfor %}
    {{ return(
        "((" ~ date_col ~ " >= date '" ~ months[0] ~ "' and " ~ date_col ~ " < date '" ~ months[-1] ~ "' + interval 1 month"
        ~ " and date_trunc('month', " ~ date_col ~ ") in (" ~ month_list | join(", ") ~ ")) or " ~ new_without_date ~ ")"
    ) }}
{% endmacro %}


{#
  Bounded incremental scope in BLOCKS OF N DAYS (default a week), for the FIRMS models whose key includes the day: a detection,
  a pass, a hexagon-day. A block is pending when its newest source load is newer than the newest one the model holds for
  that block; at most `max_blocks` pending blocks, oldest first, are taken per run and the rest stays pending. A month of the
  whole world is ~6 M detections, which needs about 4 GB of DuckDB's memory in the heaviest models: a week needs a fourth.

  Blocks are aligned to Mondays (2000-01-03), so a 7-day block is an ISO week. The model must delete by day, not by month:
  unique_key='acq_date' (a month key would erase the other weeks of the month). The target needs the same date column.

    source_sql        the model's source query (rows to filter)
    date_col          date column of the source rows and of {{ this }}
    block_days        days per block
    max_blocks        blocks per run
    source_load_col   load id column in the source rows        (default _dlt_load_id)
    target_load_col   load id column stored in {{ this }}      (default _dlt_load_id)
    undated           also take rows WITHOUT a date (see pending_months_scope)
#}
{% macro block_start_sql(date_expr, block_days) -%}
(date '2000-01-03' + (floor(date_diff('day', date '2000-01-03', {{ date_expr }}) / {{ block_days }}.0)::bigint * {{ block_days }})::integer)
{%- endmacro %}

{% macro pending_blocks_scope(source_sql, date_col, block_days, max_blocks, source_load_col='_dlt_load_id', target_load_col='_dlt_load_id', undated=False) %}
    {% if not execute %}
        {{ return("true") }}
    {% endif %}
    {% set blk = block_start_sql(date_col, block_days) %}
    {% set target_blocks %}
        {% if is_incremental() %}
        select {{ blk }} as b, max({{ target_load_col }}) as l from {{ this }} where {{ date_col }} is not null group by 1
        {% else %}
        select null::date as b, null::varchar as l where false
        {% endif %}
    {% endset %}
    {% set pending = run_query(
        "with s as (select " ~ blk ~ " as b, max(" ~ source_load_col ~ ") as l "
        ~ "from (" ~ source_sql ~ ") where " ~ date_col ~ " is not null group by 1), "
        ~ "t as (" ~ target_blocks ~ ") "
        ~ "select strftime(s.b, '%Y-%m-%d') from s left join t using (b) where t.l is null or s.l > t.l order by 1"
    ).columns[0].values() %}
    {% set blocks = pending[:max_blocks] %}
    {% do log(this ~ ": " ~ (pending | length) ~ " pending blocks of " ~ block_days ~ " days, this run: " ~ (blocks | join(", ") or "none"), info=true) %}
    {% set undated_sql = "false" %}
    {% if undated %}
        {% if is_incremental() %}
            {% set watermark = run_query("select max(" ~ target_load_col ~ ") from " ~ this).columns[0].values()[0] %}
            {% set undated_sql = "(" ~ date_col ~ " is null and " ~ source_load_col ~ " > '" ~ watermark ~ "')" if watermark is not none else date_col ~ " is null" %}
        {% else %}
            {% set undated_sql = date_col ~ " is null" %}
        {% endif %}
    {% endif %}
    {% if blocks | length == 0 %}
        {{ return(undated_sql) }}
    {% endif %}
    {% set block_list = [] %}
    {% for b in blocks %}
        {% do block_list.append("date '" ~ b ~ "'") %}
    {% endfor %}
    {% set dated_sql =
        "(" ~ date_col ~ " >= date '" ~ blocks[0] ~ "' and " ~ date_col ~ " < date '" ~ blocks[-1] ~ "' + interval " ~ block_days ~ " day"
        ~ " and " ~ blk ~ " in (" ~ block_list | join(", ") ~ "))" %}
    {{ return("(" ~ dated_sql ~ " or " ~ undated_sql ~ ")" if undated else dated_sql) }}
{% endmacro %}


{# Union of several relations by column name (models of the same layer share one schema). #}
{% macro union_relations_sql(model_names) %}
    {% for m in model_names %}
    select * from {{ ref(m) }}
    {% if not loop.last %}union all by name{% endif %}
    {% endfor %}
{% endmacro %}


{#
  Bounded incremental scope for the big weather models: the months whose newest source
  load is newer than the newest one the model already has FOR THAT MONTH, oldest first,
  at most `max_months` per run. Returns a SQL predicate on the source rows.

  Unlike incremental_months_scope (one global watermark), months left for later stay
  pending: their source load is still newer than what the model has for them, whatever
  else ran in between. So a backlog (a long backfill landing before dbt runs, or dbt
  failing for a while) is worked through in steps that fit in memory, instead of one
  query over every month at once (which ran DuckDB out of its 4 GB).

  The first build (no table yet) and a --full-refresh also take only the oldest
  `max_months`; later runs take the rest.

    source_sql        the model's source query (rows to filter)
    date_col          date column of the source rows
    max_months        months per run
    source_load_col   load id column in the source rows        (default _dlt_load_id)
    target_load_col   load id column stored in {{ this }}      (default _dlt_load_id)
    month_col         month column stored in {{ this }}        (default obs_month)
    undated           also take rows WITHOUT a date, which belong to no month: the ones loaded after the newest load of
                      {{ this }} (every row of the first build). FIRMS keeps invalid rows flagged in dq_issue, undated
                      ones included; the weather models drop them (default false)
#}
{% macro pending_months_scope(source_sql, date_col, max_months, source_load_col='_dlt_load_id', target_load_col='_dlt_load_id', month_col='obs_month', undated=False) %}
    {% if not execute %}
        {{ return("true") }}
    {% endif %}
    {% set target_months %}
        {% if is_incremental() %}
        select {{ month_col }} as m, max({{ target_load_col }}) as l from {{ this }} group by 1
        {% else %}
        select null::date as m, null::varchar as l where false
        {% endif %}
    {% endset %}
    {% set pending = run_query(
        "with s as (select date_trunc('month', " ~ date_col ~ ")::date as m, max(" ~ source_load_col ~ ") as l "
        ~ "from (" ~ source_sql ~ ") where " ~ date_col ~ " is not null group by 1), "
        ~ "t as (" ~ target_months ~ ") "
        ~ "select strftime(s.m, '%Y-%m-%d') from s left join t using (m) where t.l is null or s.l > t.l order by 1"
    ).columns[0].values() %}
    {% set months = pending[:max_months] %}
    {% do log(this ~ ": " ~ (pending | length) ~ " pending months, this run: " ~ (months | join(", ") or "none"), info=true) %}
    {% set undated_sql = "false" %}
    {% if undated %}
        {% if is_incremental() %}
            {% set watermark = run_query("select max(" ~ target_load_col ~ ") from " ~ this).columns[0].values()[0] %}
            {% set undated_sql = "(" ~ date_col ~ " is null and " ~ source_load_col ~ " > '" ~ watermark ~ "')" if watermark is not none else date_col ~ " is null" %}
        {% else %}
            {% set undated_sql = date_col ~ " is null" %}
        {% endif %}
    {% endif %}
    {% if months | length == 0 %}
        {{ return(undated_sql) }}
    {% endif %}
    {% set month_list = [] %}
    {% for m in months %}
        {% do month_list.append("date '" ~ m ~ "'") %}
    {% endfor %}
    {% set dated_sql =
        "(" ~ date_col ~ " >= date '" ~ months[0] ~ "' and " ~ date_col ~ " < date '" ~ months[-1] ~ "' + interval 1 month"
        ~ " and date_trunc('month', " ~ date_col ~ ") in (" ~ month_list | join(", ") ~ "))" %}
    {{ return("(" ~ dated_sql ~ " or " ~ undated_sql ~ ")" if undated else dated_sql) }}
{% endmacro %}
