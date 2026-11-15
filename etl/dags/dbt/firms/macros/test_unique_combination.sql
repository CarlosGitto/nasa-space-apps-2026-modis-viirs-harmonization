{#
  Fails if any combination of `columns` appears more than once.

  recent_only: check only the acquisition months touched by the latest load
  (the ones the last incremental run recomputed), instead of grouping years of
  data on every run. Needs acq_month and a load id column (load_col).
#}
{% test unique_combination(model, columns, recent_only=false, load_col='_dlt_load_id') %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
{% if recent_only %}
where acq_month in (
    select distinct acq_month from {{ model }}
    where {{ load_col }} = (select max({{ load_col }}) from {{ model }})
)
{% endif %}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
