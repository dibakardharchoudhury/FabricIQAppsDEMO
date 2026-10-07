import { AGENT_NAMES, agentDefinition } from '../src/services/copilot/agentDefinitions.ts'
import type { AgentRole } from '../src/services/copilot/orchestration.ts'

const [model, fabricIqConnection, ontologyConnection] = process.argv.slice(2)
if (!model || !fabricIqConnection || !ontologyConnection) throw new Error('Model deployment and both verified Fabric IQ connections are required.')
console.log(JSON.stringify(Object.fromEntries(
  (Object.keys(AGENT_NAMES) as AgentRole[]).map(role => [AGENT_NAMES[role], agentDefinition(role, model, fabricIqConnection, ontologyConnection)]),
)))
