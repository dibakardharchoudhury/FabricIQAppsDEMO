export type AgentBattleEngine = 'data-agent' | 'foundry'
export type AgentBattleMode = 'sequential' | 'parallel'

export type AgentBattleResult<T> = {
  engine: AgentBattleEngine
  ok: boolean
  elapsedMs: number
  value?: T
  error?: string
}

export type AgentBattleRunners<T> = Record<AgentBattleEngine, (prompt: string) => Promise<T>>

export async function runAgentBattle<T>(
  prompt: string,
  mode: AgentBattleMode,
  runners: AgentBattleRunners<T>,
  onSettled?: (result: AgentBattleResult<T>) => void,
): Promise<Record<AgentBattleEngine, AgentBattleResult<T>>> {
  const exactPrompt = prompt.trim()
  if (!exactPrompt) throw new Error('A prompt is required to run an agent comparison.')

  const run = async (engine: AgentBattleEngine): Promise<AgentBattleResult<T>> => {
    const startedAt = Date.now()
    let result: AgentBattleResult<T>
    try {
      const value = await runners[engine](exactPrompt)
      result = { engine, ok: true, elapsedMs: Date.now() - startedAt, value }
    } catch (error) {
      result = {
        engine,
        ok: false,
        elapsedMs: Date.now() - startedAt,
        error: error instanceof Error ? error.message : 'The agent request failed.',
      }
    }
    onSettled?.(result)
    return result
  }

  if (mode === 'parallel') {
    const [dataAgent, foundry] = await Promise.all([run('data-agent'), run('foundry')])
    return { 'data-agent': dataAgent, foundry }
  }

  const dataAgent = await run('data-agent')
  const foundry = await run('foundry')
  return { 'data-agent': dataAgent, foundry }
}
