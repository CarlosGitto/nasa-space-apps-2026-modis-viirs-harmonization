// NASA GIBS layers available in the app.
// Verify ids and dates in the GIBS catalog: a wrong id shows an empty map, not an error.
export const GIBS_LAYERS = [
    // VIIRS
    { id: 'VIIRS_NOAA20_CorrectedReflectance_TrueColor', label: 'VIIRS NOAA-20', from: '2018-01-01' },
    { id: 'VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1', label: 'VIIRS NOAA-20', from: '2018-01-01' },
    { id: 'VIIRS_SNPP_CorrectedReflectance_TrueColor', label: 'VIIRS S-NPP', from: '2012-01-20', to: '2026-11-02' },
    { id: 'VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1', label: 'VIIRS S-NPP', from: '2012-01-20', to: '2026-11-02' },
    // MODIS
    { id: 'MODIS_Aqua_CorrectedReflectance_TrueColor', label: 'MODIS Aqua', from: '2002-07-04' },
    { id: 'MODIS_Aqua_CorrectedReflectance_Bands721', label: 'MODIS Aqua', from: '2002-07-04' },
    { id: 'MODIS_Terra_CorrectedReflectance_TrueColor', label: 'MODIS Terra', from: '2000-02-24' },
    { id: 'MODIS_Terra_CorrectedReflectance_Bands721', label: 'MODIS Terra', from: '2000-02-24' },
]

// Image types the user can choose. For each one, satellites in order of preference.
export const IMAGE_TYPES = {
    truecolor: {
        labelKey: 'gibs.truecolor',
        layers: [
            'VIIRS_NOAA20_CorrectedReflectance_TrueColor',
            'VIIRS_SNPP_CorrectedReflectance_TrueColor',
            'MODIS_Aqua_CorrectedReflectance_TrueColor',
            'MODIS_Terra_CorrectedReflectance_TrueColor',
        ],
    },
    fire: {
        labelKey: 'gibs.fire',
        layers: [
            'VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1',
            'VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1',
            'MODIS_Aqua_CorrectedReflectance_Bands721',
            'MODIS_Terra_CorrectedReflectance_Bands721',
        ],
    },
}

export const gibsUrl = (layerId, date) =>
    `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${layerId}/default/${date}` +
    `/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`

// Yesterday in UTC: today's image is often not processed yet.
export const yesterdayUTC = () => new Date(Date.now() - 864e5).toISOString().slice(0, 10)

// Date to use for a layer, or null if it has no imagery for that day.
export function gibsDate(layer, wanted) {
    const d = (wanted && wanted < yesterdayUTC()) ? wanted : yesterdayUTC()
    if (d < layer.from) return null
    if (layer.to && d > layer.to) return null
    return d
}

// First preferred layer of that image type with imagery for the date.
// Returns { layer, date } or null.
export function pickLayer(type, wanted) {
    const ids = IMAGE_TYPES[type]?.layers ?? []
    for (const id of ids) {
        const layer = GIBS_LAYERS.find((l) => l.id === id)
        const date = layer && gibsDate(layer, wanted)
        if (date) return { layer, date }
    }
    return null
}