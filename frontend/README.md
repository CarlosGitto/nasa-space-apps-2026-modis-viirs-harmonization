# frontend

Map of fire detections for the FIRMS data served by `backend/`.

Stack: **Vue 3 + Vite** (with Pinia for the shared state), **MapLibre GL JS**
(map), **Terra Draw** (draw a zone), **Apache ECharts** (burn calendar). Plain
JavaScript.

## Run

From the repo root, with the backend running:

```bash
make backend-dev      # API on :8000
make frontend-setup   # once
make frontend-dev     # http://localhost:5173
```

In dev, `/api/*` is proxied to `http://localhost:8000` (override with
`API_TARGET`). For a deployed build, set `VITE_API_URL` to the backend URL.
`VITE_MAP_STYLE` changes the basemap (default: OpenFreeMap Positron, no API key).

## How it works

- **State** ([src/stores/filters.js](src/stores/filters.js)): dataset, dates and
  zone. `zone = null` means the whole world; drawing a zone restricts the map and
  the calendar, clearing it goes back to the world.
- **Map** ([src/components/FireMap.vue](src/components/FireMap.vue)): H3 hexagons
  (`/stats/hex`) at every zoom: coarse parents when zoomed out, the fixed res-6
  grid (~36 km²) from zoom 7.5. From zoom 10 the hexagon border turns dashed, the
  fill fades and individual detections (`/detections`, up to 5000) are drawn on
  top. Only the visible area is requested. Hover shows the numbers.
- **Grid** ([src/hexGrid.js](src/hexGrid.js)): every cell of the visible area is
  drawn (borders only), with or without detections, so the map always shows the
  same fixed grid. Built in the browser with `h3-js` (same ids as the backend);
  empty cells are not clickable.
- **Hexagons**: clicking a coarse hexagon zooms into it (at least one level down).
  Clicking a res-6 hexagon opens the side panel
  ([src/components/hexagon/](src/components/hexagon/)); Shift/Ctrl/Cmd + click
  adds or removes hexagons, Esc closes. Tabs: **Observaciones** (table of
  detections for the dataset and dates, per satellite), **Ficha · historial**
  (whole history, precomputed), **Ficha · período** (the selected dates, with a
  compact weather summary, `HexWeather.vue`) and **Clima** (`HexClimate.vue`): the
  hexagon's weather day by day, at least 30 days up to the end date, with the time
  slider's day marked: fire detections per sensor against the maximum VPD,
  temperature and dew point, minimum humidity and wind (with direction in the
  tooltip), daily rain with its 30-day total and days since rain. NASA MERRA-2 and
  GPM IMERG, from the grid cell nearest to the hexagon. With
  several hexagons, "Detalle" shows one ficha each; "Agrupado" is a work in progress.
- **Weather layers**: "Weather layers" in the side panel turns on, over the hexagons,
  the day the map shows (the time slider's day, else the end of the range):
  **Temperature** (daily max, NASA MERRA-2 cells), **Rain** (daily total, NASA GPM
  IMERG; dry cells hidden) and **Wind** (arrows pointing where it blows, colored and
  sized by speed; overlapping ones are dropped when zoomed out). Each has a legend;
  hovering shows the values. A day without data for a layer says which days it has,
  with a button to jump there. Playing the time slider animates them. See
  [src/mapWeather.js](src/mapWeather.js) and `GET /weather/map`.
- **Area**: "Area (4 points)" on the map: click 4 points and every res-6 hexagon
  whose center is inside is selected, empty ones too (up to 5000; a bigger area only
  filters). The area snaps to those hexagons, filters the map, calendar and slider,
  and opens them in the panel. One area at a time; Esc cancels while drawing.
- **Panel with many hexagons**: Observaciones in one request; the fichas become a
  list, busiest first, that follows the dates and the time slider (a hexagon of the
  area that burns on the day shown comes up), with each ficha on click.
- **Time slider ▶**: the next day comes only once the visible map (or both curtain
  sides) has fully drawn the current one, data and imagery.
- **Burn calendar** ([src/components/BurnCalendar.vue](src/components/BurnCalendar.vue)):
  detections per month × year (`/stats/monthly`) for the dataset and zone.
  Clicking a month sets the map dates to that month.

## Manual check

1. On load: world map with hexagons and the world burn calendar, nothing drawn.
2. Click a hexagon: the map zooms into it. Repeat until the base grid (res 6), then
   click one: the panel opens with its observations and fichas. Shift + click a
   neighbor: two fichas under "Detalle". Zoom past 10: dashed borders, points on top.
3. "Area (4 points)" and click 4 points: the area snaps to its hexagons, the panel opens with
   them, and map and calendar only show that area; "Clear area" goes back to the world.
4. Pick `MODIS SP` and click a month in the calendar: the dates change to that month.
