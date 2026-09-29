import type { OntologyDefinition } from './ontologyContract'

export async function waitForDefinitionResult(response: Response, token: string): Promise<OntologyDefinition> {
  if (response.status === 200) return await response.json() as OntologyDefinition
  if (response.status !== 202) throw new Error(`Ontology definition request failed (${response.status}).`)
  const operationUrl = response.headers.get('Location') ?? response.headers.get('Operation-Location')
  if (!operationUrl) throw new Error('Ontology definition operation did not return a location.')
  const resultUrl = new URL(operationUrl)
  resultUrl.pathname = `${resultUrl.pathname.replace(/\/$/, '')}/result`
  for (let attempt = 0; attempt < 30; attempt++) {
    const statusResponse = await fetch(operationUrl, { headers: { Authorization: `Bearer ${token}` } })
    if (!statusResponse.ok) throw new Error(`Ontology definition operation failed (${statusResponse.status}).`)
    const status = await statusResponse.json() as { status?: string }
    if (/failed|cancelled/i.test(status.status ?? '')) throw new Error(`Ontology definition operation ${status.status}.`)
    if (/succeeded|completed/i.test(status.status ?? '')) {
      const resultResponse = await fetch(resultUrl.href, { headers: { Authorization: `Bearer ${token}` } })
      if (!resultResponse.ok) throw new Error(`Ontology definition result failed (${resultResponse.status}).`)
      return await resultResponse.json() as OntologyDefinition
    }
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error('Ontology definition operation timed out.')
}
