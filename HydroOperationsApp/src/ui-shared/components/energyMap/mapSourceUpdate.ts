import type { GeoJSONSource } from 'maplibre-gl'

type GeoJSONData = Parameters<GeoJSONSource['setData']>[0]

export function setGeoJSONSourceData(
  source: { setData(data: GeoJSONData): unknown },
  data: GeoJSONData,
  onError: (reason: unknown) => void,
) {
  try {
    void Promise.resolve(source.setData(data)).catch(onError)
  } catch (reason) {
    onError(reason)
  }
}
