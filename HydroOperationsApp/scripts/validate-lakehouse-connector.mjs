import { readFile } from 'node:fs/promises'
import { ConnectorsRayfinClient } from '@microsoft/rayfin-client'
import { signInWithEntraToken } from '@microsoft/rayfin-auth-provider-fabric'
import { parseEnvContent } from '@microsoft/rayfin-tools-common/_internal/config'
import { collectLakehousePages, lakehouseConnectorConfig } from '../src/services/lakehouseConnector.ts'

const values = parseEnvContent(await readFile(new URL('../rayfin/.env', import.meta.url), 'utf8'))
const apiUrl = values.get('RAYFIN_PUBLIC_API_URL')?.replace(/\/$/, '')
const origin = process.env.HYDRO_CONNECTOR_ORIGIN
const token = process.env.HYDRO_CONNECTOR_ENTRA_TOKEN
if (!apiUrl || !origin || !token || !values.get('RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT_ID')) {
  throw new Error('Delegated Lakehouse connector validation requires verified deployment identity, origin and token.')
}
const path = '/connectors/hydrolakehouse/graphql'
const originalFetch = globalThis.fetch
const preflight = await originalFetch(`${apiUrl}${path}`, {
  method: 'OPTIONS',
  headers: { Origin: origin, 'Access-Control-Request-Method': 'POST',
    'Access-Control-Request-Headers': 'authorization,content-type,x-publishable-key' },
  signal: AbortSignal.timeout(60_000),
})
const allowed = (preflight.headers.get('Access-Control-Allow-Headers') ?? '').toLowerCase().split(',').map(value => value.trim())
if (!preflight.ok || !['*', origin].includes(preflight.headers.get('Access-Control-Allow-Origin'))
  || !['authorization', 'content-type', 'x-publishable-key'].every(header => allowed.includes(header))) {
  throw new Error(`Lakehouse connector browser preflight failed (HTTP ${preflight.status}).`)
}
globalThis.fetch = async (input, init) => {
  const url = typeof input === 'string' || input instanceof URL ? String(input) : input.url
  if (!url.startsWith(`${apiUrl}/`)) throw new Error('Connector validation refused a request outside the verified AppBackend.')
  const headers = new Headers(init?.headers)
  headers.set('Origin', origin)
  const response = await originalFetch(input, { ...init, headers, signal: AbortSignal.timeout(60_000) })
  if (!['*', origin].includes(response.headers.get('Access-Control-Allow-Origin'))) {
    throw new Error(`Lakehouse connector browser response omitted CORS (HTTP ${response.status}).`)
  }
  return response
}
const client = new ConnectorsRayfinClient({
  baseUrl: `${apiUrl}/`, publishableKey: values.get('RAYFIN_PUBLIC_PUBLISHABLE_KEY'),
  useProxy: false, authStorage: false, connectors: { hydrolakehouse: lakehouseConnectorConfig },
})
try {
  await signInWithEntraToken(client.auth, { entraToken: token })
  const counts = {}
  for (const [entity, columns] of Object.entries(lakehouseConnectorConfig.entities)) {
    const rows = await collectLakehousePages(cursor => {
      const query = client.connectors.hydrolakehouse[entity].select([...columns]).first(500)
      return (cursor ? query.after(cursor) : query).executePaginated()
    })
    if (entity.startsWith('Silver') && !rows.length) throw new Error(`Lakehouse connector ${entity} returned no seeded reference rows.`)
    counts[entity] = rows.length
  }
  console.log(`LAKEHOUSE_CONNECTOR_READBACK=${JSON.stringify(counts)}`)
} finally {
  client.auth.destroy()
  globalThis.fetch = originalFetch
}
