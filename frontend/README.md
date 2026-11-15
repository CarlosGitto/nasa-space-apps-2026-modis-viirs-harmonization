# frontend

A **fire calendar** of the FIRMS data served by `backend/`: the calendar says *when* it burned (2000 → today, on
the harmonized MODIS + VIIRS series), the map next to it says *where*.

Stack: **Vue 3 + Vite** (with Pinia for the shared state), **MapLibre GL JS**
(map), **Apache ECharts** (hexagon charts). Plain JavaScript.

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
- **Split view** ([src/components/CompareMap.vue](src/components/CompareMap.vue)): two synced maps
  split by a draggable handle, without the time slider. *Epochs* (default): the same place in two
  periods, **months or years**, each side the aggregate of its period; harmonized by default (one
  series in MODIS units, `GET /stats/harmonized/hex`) or the chosen dataset's raw values. Periods
  are always inside the chosen dataset's coverage (partial ones are marked); the change is per day
  when the lengths differ. Optional imagery of each period's last day. *MODIS | VIIRS*: both
  sensors over the panel's whole date range, how each sees the same fire.
- **Color scale**: hexagons are colored by fire per 1,000 km² per day, on a scale **relative to
  what was asked for** (percentiles of the query), so long ranges and far zooms don't wash out.
  The main map keeps the range's scale while the time slider plays; the split view shares one
  scale between its sides. A small legend shows the values. Borders are a whole pixel (not a
  fraction), light, so they look alike on every screen scale.
- **Side panel**: folds to a thin rail (the handle on its edge) that still shows the dataset and
  dates; remembered in the browser.
- **Mouse coordinates**: bottom right of the map.
- **Time slider ▶**: the next day comes only once the visible map (or both curtain
  sides) has fully drawn the current one, data and imagery.
- **Contour lines** ([src/relief.js](src/relief.js)): "Contour lines" on the map toolbar,
  remembered in the browser. Drawn in the browser by `maplibre-contour` from the public
  AWS Terrain Tiles (mostly NASA SRTM): nothing is ingested or stored. Off, nothing is
  requested; on, only the visible area, cached. Spaced out by zoom (1000 m from zoom 6,
  500 m from 9, 100 m from 11, 50 m from 13), olive, under the fire hexagons, whose borders
  turn neutral gray while the contours are on.
- **Loading bar** ([src/components/LoadingBar.vue](src/components/LoadingBar.vue),
  [src/stores/loading.js](src/stores/loading.js)): a 3 px line at the very top that grows
  from the left while something loads (fast at first, then slower, never finishing by
  itself) and completes only when the load is really done (for the map: from the moment it
  stops until the new hexagons are drawn), in the color of what loads: fire orange (hexagons), blue (hexagon
  panel), MODIS yellow → VIIRS cyan (curtain), olive (contour lines), sky blue
  (satellite imagery), amber (calendar and slider), gray (basemap); several at once
  blend. Shows only after 150 ms, so quick loads don't flicker; full and still with reduced motion.
  New loads join with `useLoadingBar(kind, loadingRef)`, or `trackMapSources` for map tiles.
- **Fire calendar** ([src/components/FireCalendar.vue](src/components/FireCalendar.vue)): the app's main
  control, at the top of the left panel. It picks the period the map shows.
  - **Values**: *Harmonized* (one series 2000 → today in MODIS units, `/stats/harmonized/{monthly,daily}`; the
    default once `make etl-harmonize` has built it) or *Raw* (the chosen dataset as published). The switch is
    global: the main map and the time slider follow it.
  - **Zoom levels**: *Years* (years × months, each year's total as a bar and a *Typical* row = the mean of each
    month: which years and which seasons burn most), *Year* (twelve month calendars, one square per day) and
    *Month* (a big calendar with each day's count). ‹ › step a year or a month.
  - **Picking**: click a month (or a year label) to show it on the map, double-click to open its days; click a
    day to show it (its month becomes the slider's range); drag over days or Shift + click for a range. Arrow
    keys move between cells, Enter picks.
  - **Color**: hotspots or FRP, linear or log, relative to the busiest cell in view; or *vs. typical*: each month
    against the same month of the other years (a day against a typical day of its month), blue below, red above.
  - **Region** ([src/regions.js](src/regions.js)): *Follow the map* (default: the calendar counts what is on
    screen, so zooming the map narrows it), the whole world, hemispheres, continents and fire regions (Amazon,
    Southern Africa, Australia…). A preset filters the calendar and the map and zooms the map to it; a drawn
    area works the same.
  - **On the map** box: the period in numbers, its peak day, and for a single month how it compares with the
    same month of the other years and its rank. Downloads: *Calendar CSV* (one row per day, in the calendar's
    values) and *Hotspots CSV* (every original detection of the period and region, `detections.csv`).
- **Period badge** (top of the map): the calendar's period (and the slider's day) the map is showing.
- **3D** (map toolbar): the fire hexagons rise as columns whose height follows the same level as their color,
  and the map tilts.

## Languages (English / Spanish)

[vue-i18n](https://vue-i18n.intlify.dev/). Only what the user reads is translated: code,
API, data and technical names (SP, NRT, MODIS, NOAA-20, H3 ids) stay as they are.

- Texts live in [src/locales/en.json](src/locales/en.json) and [src/locales/es.json](src/locales/es.json),
  with the same keys. To add a text: add the key to **both** files and use
  `{{ $t('area.key') }}` in a template (`useI18n().t` in a component's script,
  `i18n.global.t` elsewhere).
- Numbers and dates: the helpers in [src/format.js](src/format.js) (`num`, `pct`, `date`),
  never `toLocaleString` with a fixed language.
- Write keys literally (`cond ? $t('a') : $t('b')`, not `$t(cond ? 'a' : 'b')`) so the
  linter can check them. Families built from a value on purpose (`panel.tabs.${id}`) are
  listed in [eslint.config.js](eslint.config.js).
- The language starts from the browser (Spanish if it's in Spanish, else English) and
  the user's choice (ES | EN, top left) is remembered in the browser.

## Accessibility

Target: WCAG 2.2 AA, following the WAI-ARIA authoring practices on top of native HTML.

- **Accessibility menu** (universal-access icon, top left), saved in the browser:
  - text size 100 / 125 / 150 % (every `font-size` is in `rem`: use rem for new ones too);
  - high contrast (color tokens in [src/style.css](src/style.css): use `var(--…)`, not fixed colors, for UI);
  - reduce motion (starts on if the system asks for it);
  - easy reading: Atkinson Hyperlegible font, wider text spacing, patterns in charts.
- **Keyboard**: "Skip to map" link; on the focused map, arrows move, +/− zoom,
  **Enter** acts at the center (crosshair) like a click, **Shift+Enter** adds a hexagon,
  Esc cancels a drawing. The panel's tabs move with the arrows, Home and End; opening
  the panel focuses it and closing it (✕ or Esc) returns the focus.
- **Screen readers**: map status, area notes and loading are announced; charts have a text
  summary; every fire calendar cell is a button whose label says its value; tables have captions.
- **Lint**: `make frontend-lint` fails on text written straight into a template, keys
  missing from (or unused in) the locales, and common accessibility mistakes.

## Manual check

1. On load: the fire calendar (Years level) next to a world map with hexagons, nothing drawn. Click a month: the
   map shows it and its badge says so; double-click: its days. Pick "South America": the map zooms there and the
   calendar recounts. Zoom the map with "Follow the map": the calendar recounts what's on screen.
2. Click a hexagon: the map zooms into it. Repeat until the base grid (res 6), then
   click one: the panel opens with its observations and fichas. Shift + click a
   neighbor: two fichas under "Detalle". Zoom past 10: dashed borders, points on top.
3. "Area (4 points)" and click 4 points: the area snaps to its hexagons, the panel opens with
   them, and map and calendar only show that area; "Clear area" goes back to the world.
4. ES | EN switches every text, number and date format; reload keeps it.
5. Accessibility menu: each option changes the page and stays after a reload. Tab from
   the top: "Skip to map", then the map; Enter on a hexagon opens the panel.
6. Pick `MODIS SP` and click a month in the calendar: the dates change to that month.
