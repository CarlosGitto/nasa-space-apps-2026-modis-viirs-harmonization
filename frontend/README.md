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
  (`/stats/hex`) below zoom 7, individual detections (`/detections`, up to 5000)
  from zoom 7. Only the visible area is requested.
- **Zone**: Polygon or Rectangle tool on the map (one zone at a time).
- **Burn calendar** ([src/components/BurnCalendar.vue](src/components/BurnCalendar.vue)):
  detections per month × year (`/stats/monthly`) for the dataset and zone.
  Clicking a month sets the map dates to that month.

## Manual check

1. On load: world map with hexagons and the world burn calendar, nothing drawn.
2. Zoom in past level 7: hexagons turn into individual detections (click one for details).
3. Draw a rectangle: map and calendar only show that area; "Borrar zona" goes back to the world.
4. Pick `MODIS SP` and click a month in the calendar: the dates change to that month.
