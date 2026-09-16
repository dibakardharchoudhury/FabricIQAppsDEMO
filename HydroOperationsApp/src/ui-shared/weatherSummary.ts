export type PrecipitationBucket = {
  valid_time_utc: string
  precipitation_interval_hours?: number | null
  precipitation?: number | null
  cumulative_precipitation?: number | null
  rainfall_volume_m3?: number | null
  cumulative_rainfall_volume_m3?: number | null
}

export type PrecipitationWindowSummary = {
  amount?: number
  volume?: number
  coveredHours: number
}

const finiteValue = (value?: number | null) => value == null || !Number.isFinite(Number(value)) ? undefined : Number(value)

const sumValues = (buckets: PrecipitationBucket[], field: 'precipitation' | 'rainfall_volume_m3') => {
  const values = buckets.flatMap(bucket => {
    const value = finiteValue(bucket[field])
    return value == null ? [] : [value]
  })
  return values.length ? values.reduce((sum, value) => sum + value, 0) : undefined
}

export function summarizePrecipitation(
  buckets: PrecipitationBucket[],
  timeAnchor: number,
  windowHours: number,
): PrecipitationWindowSummary | undefined {
  const windowEnd = timeAnchor + windowHours * 3_600_000
  const ordered = [...buckets].sort((left, right) => Date.parse(left.valid_time_utc) - Date.parse(right.valid_time_utc))
  const includedIndexes = ordered.flatMap((bucket, index) => {
    const bucketEnd = Date.parse(bucket.valid_time_utc)
    const intervalHours = Number(bucket.precipitation_interval_hours)
    if (!Number.isFinite(bucketEnd) || !Number.isFinite(intervalHours) || intervalHours <= 0) return []
    const bucketStart = bucketEnd - intervalHours * 3_600_000
    return bucketStart >= timeAnchor && bucketEnd <= windowEnd ? [index] : []
  })
  if (!includedIndexes.length) return undefined

  const firstIndex = includedIndexes[0]
  const lastIndex = includedIndexes[includedIndexes.length - 1]
  const included = includedIndexes.map(index => ordered[index])
  const predecessor = firstIndex > 0 ? ordered[firstIndex - 1] : undefined
  const contiguous = includedIndexes.every((index, offset) => {
    if (index !== firstIndex + offset || index === 0) return false
    const currentEnd = Date.parse(ordered[index].valid_time_utc)
    const previousEnd = Date.parse(ordered[index - 1].valid_time_utc)
    const intervalMs = Number(ordered[index].precipitation_interval_hours) * 3_600_000
    return Number.isFinite(currentEnd)
      && Number.isFinite(previousEnd)
      && Number.isFinite(intervalMs)
      && currentEnd - previousEnd === intervalMs
  })
  const difference = (to?: number | null, from?: number | null) => {
    const end = finiteValue(to)
    const start = finiteValue(from)
    return end == null || start == null ? undefined : end - start
  }
  const amount = contiguous && predecessor
    ? difference(ordered[lastIndex].cumulative_precipitation, predecessor.cumulative_precipitation)
    : undefined
  const volume = contiguous && predecessor
    ? difference(ordered[lastIndex].cumulative_rainfall_volume_m3, predecessor.cumulative_rainfall_volume_m3)
    : undefined

  return {
    amount: amount ?? sumValues(included, 'precipitation'),
    volume: volume ?? sumValues(included, 'rainfall_volume_m3'),
    coveredHours: included.reduce((sum, bucket) => sum + Number(bucket.precipitation_interval_hours), 0),
  }
}
