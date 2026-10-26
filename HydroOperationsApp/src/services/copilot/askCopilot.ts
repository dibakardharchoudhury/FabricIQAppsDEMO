import { askFoundryCopilot, askHostedCopilot } from './foundry'

/** Both comparison panes use the deployment-owned Hosted Agent runtime. */
export function askCopilot(
  engine: 'data-agent' | 'foundry',
  ...args: Parameters<typeof askFoundryCopilot>
): ReturnType<typeof askFoundryCopilot> {
  return askHostedCopilot(engine, args[0], args[1], args[3])
}
