const MAX_RECENT_QUESTIONS = 4
const MAX_CONTEXT_CHARACTERS = 1200

const FOLLOW_UP_PATTERN = /^(?:or|and|also|then|now|what about|how about)\b|\b(?:this|that|these|those|same|it|its|previous|above|earlier)\b/i

export function contextualizeDataAgentQuestion(currentQuestion: string, previousQuestions: string[]): string {
  if (!previousQuestions.length || !FOLLOW_UP_PATTERN.test(currentQuestion.trim())) return currentQuestion

  const recent: string[] = []
  let length = 0
  for (const question of previousQuestions.slice(-MAX_RECENT_QUESTIONS).reverse()) {
    const nextLength = length + question.length
    if (recent.length && nextLength > MAX_CONTEXT_CHARACTERS) break
    recent.unshift(question)
    length = nextLength
  }

  return [
    '<recent_user_questions>',
    ...recent.map((question, index) => `${index + 1}. ${question}`),
    '</recent_user_questions>',
    '<current_user_question>',
    currentQuestion,
    '</current_user_question>',
  ].join('\n')
}
