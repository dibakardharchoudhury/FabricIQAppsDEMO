import { ArrowLeftRight } from 'lucide-react'
import { safeSourceUrl, sourceAge, type EnergyFeature } from '../../energyMapModel'

function power(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} MW` : 'Unavailable'
}

function time(value: unknown): string {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return 'Not provided'
  return new Date(value).toLocaleString()
}

export function CountryPowerBalanceTile({ feature, error, now }: {
  feature?: EnergyFeature | null; error?: string; now: number
}) {
  const properties = feature?.properties
  const observed = feature?.observedAt
  const stale = !observed || !Number.isFinite(Date.parse(observed)) || now - Date.parse(observed) > 30 * 60_000
  const url = feature && safeSourceUrl(feature.sourceUrl)
  return <section className="energy-map-frequency-tile energy-map-balance-tile" aria-label="Country power balance">
    <div><ArrowLeftRight size={19} /><h2>Country power balance</h2><span>Norway</span></div>
    {feature === undefined && !error ? <strong>Loading...</strong> : !feature ? <strong>Unavailable</strong> : <>
      <dl className="energy-map-balance-metrics">
        <div><dt>Production</dt><dd>{power(properties?.production_mw)}</dd></div>
        <div><dt>Consumption</dt><dd>{power(properties?.consumption_mw)}</dd></div>
        <div><dt>Net exchange</dt><dd>{power(properties?.net_exchange_mw)}</dd></div>
      </dl>
      <span className={stale ? 'energy-map-stale' : ''}>Overview {sourceAge(observed ?? null, now)}{stale ? ' - stale snapshot' : ''}</span>
      <details>
        <summary>Balance details</summary>
        <dl>
          <div><dt>Hydropower</dt><dd>{power(properties?.hydro_mw)}</dd></div>
          <div><dt>Wind</dt><dd>{power(properties?.wind_mw)}</dd></div>
          <div><dt>Thermal</dt><dd>{power(properties?.thermal_mw)}</dd></div>
          <div><dt>Nuclear</dt><dd>{power(properties?.nuclear_mw)}</dd></div>
          <div><dt>Production observed</dt><dd>{time(properties?.production_observed_at)}</dd></div>
          <div><dt>Consumption observed</dt><dd>{time(properties?.consumption_observed_at)}</dd></div>
          <div><dt>Exchange observed</dt><dd>{time(properties?.net_exchange_observed_at)}</dd></div>
          <div><dt>Imported</dt><dd>{time(feature.ingestedAt)}</dd></div>
        </dl>
        <p>Metric observation times may differ. Exchange uses the provider's signed value, unchanged.</p>
        {url && <a href={url} target="_blank" rel="noreferrer">Statnett source</a>}
      </details>
    </>}
    <small>Norway-wide Statnett snapshot, independent of map filters. Use Import latest data to refresh.</small>
    {error && <p role="alert">{error} {feature && 'The previous snapshot is still shown.'}</p>}
  </section>
}
