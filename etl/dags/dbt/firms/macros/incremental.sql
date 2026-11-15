{#
  Incremental scope shared by every incremental model: the acquisition months
  that received rows from loads newer than anything the model already has.
  Returns a SQL predicate on the source rows (columns acq_date + a load id).

    source_sql        the model's source query (rows to filter)
    source_load_col   load id column in the source rows        (default _dlt_load_id)
    target_load_col   load id column already stored in {{ this }} (default _dlt_load_id)

  Recomputing whole months is correct because every key we dedup or group by
  includes acq_date: all versions of a detection, and all rows of a day, fall in
  the same month. The month list and the watermark are rendered as literals (not
  subqueries) so DuckLake can skip files using its min/max column statistics.

  _dlt_load_id is compared as text: it is "<unix seconds>.<fraction>" with a
  10-digit integer part (until year 2286), so text order matches numeric order.
#}
{% macro incremental_months_scope(source_sql, source_load_col='_dlt_load_id', target_load_col='_dlt_load_id') %}
    {% set watermark = run_query("select max(" ~ target_load_col ~ ") from " ~ this).columns[0].values()[0] %}
    {% if watermark is none %}
        {{ return("true") }}
    {% endif %}
    {% set months = run_query(
        "select distinct strftime(date_trunc('month', acq_date), '%Y-%m-%d') from (" ~ source_sql ~ ") "
        ~ "where " ~ source_load_col ~ " > '" ~ watermark ~ "' and acq_date is not null order by 1"
    ).columns[0].values() %}
    {% do log(this ~ ": watermark " ~ watermark ~ ", months to recompute: " ~ (months | join(", ") or "none"), info=true) %}
    {% set new_without_date = "(acq_date is null and " ~ source_load_col ~ " > '" ~ watermark ~ "')" %}
    {% if months | length == 0 %}
        {{ return(new_without_date) }}
    {% endif %}
    {% set month_list = [] %}
    {% for m in months %}
        {% do month_list.append("date '" ~ m ~ "'") %}
    {% endfor %}
    {{ return(
        "((acq_date >= date '" ~ months[0] ~ "' and acq_date < date '" ~ months[-1] ~ "' + interval 1 month"
        ~ " and date_trunc('month', acq_date) in (" ~ month_list | join(", ") ~ ")) or " ~ new_without_date ~ ")"
    ) }}
{% endmacro %}


{# Union of several relations by column name (models of the same layer share one schema). #}
{% macro union_relations_sql(model_names) %}
    {% for m in model_names %}
    select * from {{ ref(m) }}
    {% if not loop.last %}union all by name{% endif %}
    {% endfor %}
{% endmacro %}
