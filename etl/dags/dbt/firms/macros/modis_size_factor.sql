{#
  MODIS pixels grow towards the edge of the scan (bow-tie) and the FRP they report grows with them. FRP is
  divided by this factor, from the pixel size sqrt(scan * track) in km (median Aqua/VIIRS ratio measured on
  SNPP-Aqua pairs after merging duplicates; SUMMARY.md, stage 11b/13). Nadir pixels (~1 km) are not changed.
#}
{% macro modis_size_factor(scan, track) %}
    case when sqrt({{ scan }} * {{ track }}) < 1.2 then 1.0
         when sqrt({{ scan }} * {{ track }}) < 1.6 then 1.13
         when sqrt({{ scan }} * {{ track }}) < 2.2 then 1.15
         else 1.26 end
{% endmacro %}
