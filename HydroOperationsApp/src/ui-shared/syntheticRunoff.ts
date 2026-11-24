export function syntheticRunoffScenario() {
  return { areaKm2: 10, coefficient: 0.5, rainfallMm: [0, 4, 10, 25, 12, 3, 0] }
}

export function calculateRainfallRunoff(areaKm2: number, coefficient: number, rainfallMm: readonly number[]) {
  if (!Number.isFinite(areaKm2) || areaKm2 <= 0) throw new Error('Catchment area must be a finite number greater than zero.')
  if (!Number.isFinite(coefficient) || coefficient < 0 || coefficient > 1) throw new Error('Runoff coefficient must be between 0 and 1.')
  if (!rainfallMm.length || rainfallMm.some(value => !Number.isFinite(value) || value < 0)) {
    throw new Error('Every day needs a finite, non-negative rainfall value.')
  }
  const days = rainfallMm.map((rainfall, index) => {
    const runoffMm = rainfall * coefficient
    const volumeM3 = rainfall * areaKm2 * 1000 * coefficient
    return { day: index + 1, rainfallMm: rainfall, runoffMm, volumeM3, meanFlowM3s: volumeM3 / 86_400 }
  })
  const rainfallTotalMm = rainfallMm.reduce((sum, value) => sum + value, 0)
  const volumeTotalM3 = days.reduce((sum, day) => sum + day.volumeM3, 0)
  if (!Number.isFinite(rainfallTotalMm) || !Number.isFinite(volumeTotalM3)) throw new Error('These inputs exceed the calculator numeric range. Use smaller values.')
  return { days, rainfallTotalMm, volumeTotalM3, meanFlowM3s: volumeTotalM3 / (days.length * 86_400) }
}
