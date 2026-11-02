import { askFoundryCopilot } from './foundry'

export function askCopilot(
  ...args: Parameters<typeof askFoundryCopilot>
): ReturnType<typeof askFoundryCopilot> {
  return askFoundryCopilot(...args)
}
