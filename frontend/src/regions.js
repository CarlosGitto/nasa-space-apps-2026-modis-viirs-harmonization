// Preset regions for the fire calendar and the map: picking one filters both (the backend's `zone`, hexagons by
// their center) and zooms the map to it. Outlines are coarse on purpose: a calendar of a continent doesn't need a
// coastline, and a short polygon keeps the request small. Names are translated (locales: regions.<id>).
// [lon, lat] rings, counter-clockwise, closed by ring().

const ring = (points) => [...points, points[0]]
const box = (w, s, e, n) => ring([[w, s], [e, s], [e, n], [w, n]])

export const REGION_GROUPS = [
  {
    id: 'hemispheres',
    regions: {
      north: box(-180, 0, 180, 90),
      south: box(-180, -90, 180, 0),
      west: box(-180, -90, 0, 90),
      east: box(0, -90, 180, 90),
      tropics: box(-180, -23.44, 180, 23.44),
    },
  },
  {
    id: 'continents',
    regions: {
      southAmerica: ring([[-82, 13], [-60, 13], [-48, 0], [-34, -5], [-38, -22], [-52, -34], [-64, -56], [-76, -56], [-76, -18], [-82, -4]]),
      northAmerica: ring([[-170, 72], [-50, 72], [-50, 45], [-80, 24], [-60, 12], [-77, 7], [-92, 14], [-118, 30], [-170, 55]]),
      europe: ring([[-25, 72], [45, 72], [45, 40], [28, 35], [-10, 35], [-25, 40]]),
      africa: ring([[-18, 36], [10, 38], [33, 32], [44, 12], [52, 12], [40, -15], [33, -35], [17, -36], [-18, 15]]),
      asia: ring([[26, 42], [45, 72], [180, 72], [180, 50], [145, 40], [130, 20], [120, -11], [95, -11], [75, 5], [57, 20], [35, 12], [33, 30], [26, 36]]),
      oceania: ring([[110, -8], [155, -8], [180, -30], [180, -48], [140, -45], [110, -35]]),
    },
  },
  {
    id: 'hotspots', // places that burn every year, good for comparing seasons across 25 years
    regions: {
      amazon: box(-80, -20, -44, 6),
      granChaco: box(-66, -30, -57, -17),
      southernAfrica: box(10, -35, 41, -8),
      sahel: box(-17, 5, 40, 17),
      siberia: box(60, 50, 140, 70),
      southeastAsia: box(92, -11, 141, 25),
      australia: box(112, -44, 154, -10),
      california: box(-125, 32, -114, 42),
    },
  },
]

export const REGIONS = Object.fromEntries(
  REGION_GROUPS.flatMap((g) => Object.entries(g.regions).map(([id, coords]) => [id, { type: 'Polygon', coordinates: [coords] }])),
)

// [west, south, east, north] of a Polygon / MultiPolygon, for the map's fitBounds.
export function bbox(geometry) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  const pts = polygons.flat(2)
  const lons = pts.map((p) => p[0])
  const lats = pts.map((p) => p[1])
  return [Math.min(...lons), Math.max(-85, Math.min(...lats)), Math.max(...lons), Math.min(85, Math.max(...lats))]
}
