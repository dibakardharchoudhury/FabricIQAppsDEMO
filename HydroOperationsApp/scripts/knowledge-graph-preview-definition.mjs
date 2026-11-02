import { readFile } from 'node:fs/promises'
import { tsImport } from 'tsx/esm/api'

export async function parsePreviewDefinition(envelope) {
  const metadata = envelope?.metadata
  if (metadata?.properties?.generation !== 2) throw new Error('Saved preview requires numeric metadata.properties.generation === 2.')
  if (typeof metadata.id !== 'string' || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(metadata.id)) {
    throw new Error('Saved preview requires the Ontology item GUID in metadata.id.')
  }
  if (typeof metadata.displayName !== 'string' || !metadata.displayName.trim()) {
    throw new Error('Saved preview requires metadata.displayName.')
  }
  if (metadata.type !== undefined && metadata.type !== 'Ontology') throw new Error('Saved preview metadata.type must be Ontology.')
  for (const id of [envelope.id, envelope.definition?.id]) {
    if (id !== undefined && (typeof id !== 'string' || id.toLowerCase() !== metadata.id.toLowerCase())) {
      throw new Error('Saved preview contains conflicting Ontology item identities.')
    }
  }
  if (!Array.isArray(envelope.definition?.parts) || !envelope.definition.parts.length) {
    throw new Error('Saved preview requires definition.parts from getDefinition.')
  }
  const { parseOntologyContract } = await tsImport('../src/services/ontologyContract.ts', import.meta.url)
  const { buildOntologyModel } = await tsImport('../src/ui-shared/knowledgeGraphModel.ts', import.meta.url)
  const contract = parseOntologyContract(metadata.id, metadata.displayName, { definition: envelope.definition }, metadata.properties.generation)
  const graph = buildOntologyModel(contract)
  if (graph.error) throw new Error(graph.error)
  return contract
}

export async function loadPreviewDefinition(path) {
  return parsePreviewDefinition(JSON.parse(await readFile(path, 'utf8')))
}
