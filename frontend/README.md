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
  tooltip), daily rain with its 30-day total and days since rain. NASA POWER,
  asked on demand by the backend for the hexagon's center (any place). With
  several hexagons, "Detalle" shows one ficha each; "Agrupado" is a work in progress.
- **Weather layers**: "Weather layers" in the side panel adds the weather of the day the
  map shows (the time slider's day, else the end of the range) as icons that never paint
  the ground, so fire stays the protagonist (fire hexagons also turn more opaque and get
  a dark outline while weather is on). Material Design Icons (`@mdi/js`), drawn as SDF
  images so MapLibre colors them per value:
  **Temperature**: a thermometer colored by the daily max (usual temperature-map colors);
  **Rain**: a drop sized by the daily total (none where it's dry); **Wind**: an arrow
  pointing where it blows, sized and colored by speed. NASA POWER daily (ingested near fires).
  Each place gets one group (thermometer, arrow, drop), thinned to ~72 px. Two scopes:
  - **Around fires** (default): around the fire hexagons on screen, out to 3 rings of
    neighbors (H3), fading with the distance, and placed on the neighbors so the fire
    itself stays uncovered. Computed in the browser from the hexagons already loaded.
  - **Everywhere**: the same icons over the whole loaded area.

  **Selected hexagons get their weather on demand**, with no layer turned on and no
  need to open the Clima tab: selecting (or Shift+clicking, or drawing an area) asks
  the backend (`POST /hexagons/selection/weather`, NASA POWER, cached) for the POWER
  cells under them over the whole date range, and the map draws their icons for the
  map's day (or the latest day POWER has, ~2 days behind, as the legend says); the
  slider animates them without more requests.

  Responses are cached per layer and day (each layer comes for the whole loaded area),
  requests in flight are shared, and the
  next day is prefetched while the slider plays: panning and replaying days don't hit
  the backend again. Hovering shows the values; a day without data says which days a
  layer has, with a button to jump there. See [src/mapWeather.js](src/mapWeather.js)
  and `GET /weather/map`.
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
