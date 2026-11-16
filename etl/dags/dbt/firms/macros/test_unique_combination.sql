{#
  Fails if any combination of `columns` appears more than once.

  recent_only: check only the acquisition months touched by the latest load
  (the ones the last incremental run recomputed), instead of grouping years of
  data on every run. Needs a month column (month_col, default acq_month; the
  weather models use obs_month) and a load id column (load_col).
#}
{% test unique_combination(model, columns, recent_only=false, load_col='_dlt_load_id', month_col='acq_month') %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
{% if recent_only %}
where {{ month_col }} in (
    select distinct {{ month_col }} from {{ model }}
    where {{ load_col }} = (select max({{ load_col }}) from {{ model }})
)
{% endif %}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
