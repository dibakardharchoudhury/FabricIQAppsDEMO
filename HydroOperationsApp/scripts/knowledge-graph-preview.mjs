import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { parseArgs } from 'node:util'
import { loadPreviewDefinition } from './knowledge-graph-preview-definition.mjs'

const { values } = parseArgs({ options: { definition: { type: 'string' }, port: { type: 'string', default: '5189' } } })
if (values.definition === '') throw new Error('--definition requires a saved envelope path.')
const port = Number(values.port)
if (!/^\d+$/.test(values.port) || !Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('--port must be an integer from 1 to 65535.')
}

// Development-only declared schema fixture. This is not imported by the application.
const names = [
  'Facility', 'System', 'Equipment', 'Instrument', 'Signal', 'EquipmentType', 'SparePart',
  'WorkOrder', 'Inspection', 'Asset3DModel', 'MaintenanceNotification',
  'WeatherLocation', 'WeatherArea', 'WeatherForecast', 'EngineeringDocument', 'Organization',
]
const declarations = [
  ['System', 'Facility', 'belongs to'], ['Equipment', 'System', 'belongs to'],
  ['Instrument', 'Equipment', 'measures'], ['Signal', 'Instrument', 'describes'],
  ['Equipment', 'EquipmentType', 'has category'], ['SparePart', 'EquipmentType', 'eligible category'],
  ['WorkOrder', 'Equipment', 'concerns'], ['Inspection', 'Equipment', 'documents'],
  ['Asset3DModel', 'Equipment', 'depicts'], ['MaintenanceNotification', 'Equipment', 'reports on'],
  ['WeatherLocation', 'Facility', 'located at'], ['WeatherArea', 'WeatherLocation', 'includes location'],
  ['WeatherForecast', 'WeatherLocation', 'forecast for location'], ['WeatherForecast', 'WeatherArea', 'forecast for area'],
  ['EngineeringDocument', 'Equipment', 'documents'], ['Organization', 'Facility', 'operates'],
  ['WorkOrder', 'Instrument', 'concerns instrument'], ['Inspection', 'WorkOrder', 'references'],
  ['MaintenanceNotification', 'WorkOrder', 'references'], ['EngineeringDocument', 'System', 'documents system'],
  ['Signal', 'Equipment', 'describes equipment'],
]
const fixture = {
  id: 'preview-ontology', displayName: 'DEVELOPMENT FIXTURE — not live Fabric', generation: 2,
  entityTypes: names.map(name => ({
    id: `fixture-${name}`, name, localName: name, namespace: 'Preview',
    properties: { id: 'String', name: 'String' }, entityIdParts: ['id'],
  })),
  relationshipTypes: declarations.map(([source, target, label], index) => ({
    id: `fixture-relationship-${index}`, name: label, label,
    sourceEntityTypeId: `fixture-${source}`, targetEntityTypeId: `fixture-${target}`,
    sourceKeys: ['id'], targetKeys: ['id'], sourceCardinality: 'Many', targetCardinality: 'One',
  })),
  warnings: ['Development fixture: layout/controls only. Not a live read, materialization check, ownership assertion or compatibility certification.'],
}
const contract = values.definition ? await loadPreviewDefinition(values.definition) : fixture
const banner = values.definition ? 'SAVED DEFINITION PREVIEW — not a live Fabric read' : 'DEVELOPMENT FIXTURE — not live Fabric'
const isolateLink = values.definition ? '' : '<a href="?isolates">Isolates</a>'
const entry = `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { OntologyModelView } from '/src/ui-shared/pages/OntologyModelView.tsx'
import '/src/index.css'
import '/src/ui-shared/styles/app.css'
document.documentElement.dataset.theme = new URLSearchParams(location.search).get('theme') === 'dark' ? 'dark' : 'light'
createRoot(document.getElementById('root')).render(React.createElement(OntologyModelView))
`
const server = await createServer({
  configFile: false,
  plugins: [
    react(),
    {
      name: 'ontology-presentation-preview',
      enforce: 'pre',
      resolveId(source, importer) {
        if (source === '/@ontology-preview') return '\0ontology-preview'
        if (!importer?.replaceAll('\\', '/').endsWith('/pages/OntologyModelView.tsx')) return
        if (source === '../../services/fabric') return '\0ontology-preview-fabric'
        if (source === '../hooks/useHydroOperationsData') return '\0ontology-preview-user'
      },
      load(id) {
        if (id === '\0ontology-preview') return entry
        if (id === '\0ontology-preview-user') return 'export const useHydroOperationsData = () => ({ user: { id: "fixture-user" } })'
        if (id === '\0ontology-preview-fabric') return `
export const connectOntologyAccess = async () => {}
export const queryOntologyContract = async () => {
  if (new URLSearchParams(location.search).has('failure')) throw new Error('Deliberate preview read failure: no stale schema may remain visible.')
  const contract = ${JSON.stringify(contract)}
  if (${!values.definition} && new URLSearchParams(location.search).has('isolates')) contract.relationshipTypes = []
  return contract
}`
      },
      configureServer(vite) {
        vite.middlewares.use(async (request, response, next) => {
          if (request.url?.split('?')[0] !== '/ontology-preview') return next()
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end(await vite.transformIndexHtml('/ontology-preview', `<!doctype html><html><head><title>Ontology presentation preview</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0;padding:24px}#root .kg-page{width:100%;margin:0;transform:none}.fixture-banner{padding:10px;margin-bottom:12px;background:#fff3cd;color:#493b0b;font:14px system-ui}.fixture-banner a{margin-left:14px}</style></head><body><div class="fixture-banner">${banner}<a href="?theme=light">Light</a><a href="?theme=dark">Dark</a>${isolateLink}<a href="?failure">Read failure</a></div><div id="root"></div><script type="module" src="/@ontology-preview"></script></body></html>`))
        })
      },
    },
  ],
  server: { host: '127.0.0.1', port, strictPort: true },
})
await server.listen()
console.log(`Ontology presentation preview: http://127.0.0.1:${port}/ontology-preview`)
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => { await server.close(); process.exit(0) })
