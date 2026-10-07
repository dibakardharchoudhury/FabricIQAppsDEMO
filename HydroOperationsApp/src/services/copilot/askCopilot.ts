import { askDataAgent } from '../fabric'
import { askFoundryCopilot } from './foundry'
import { isWorkOrderRequest } from './orchestration'

/** The Fabric Data Agent is read-only; mutation requests use the real Foundry supervisor. */
export function askCopilot(
  engine: 'data-agent' | 'foundry',
  ...args: Parameters<typeof askFoundryCopilot>
): ReturnType<typeof askFoundryCopilot> {
  if (engine === 'foundry' || isWorkOrderRequest(args[0])) return askFoundryCopilot(...args)
  return askDataAgent(args[0], args[1], args[2])
}
