import { askMapDataAgent, ensureMapChatConnection } from './fabric'
import { queryLiveGridFrequency, resolveMapPlace, searchMapPlaces } from './energyMap'
import {
  isMapNavigationRequest, mapChatPrompt, navigationLayer, navigationSearch, parseMapChatAnswer,
  type MapChatContext, type MapChatTurn, type MapPlace, type MapPlaceReference,
} from '../ui-shared/mapChatModel'

export type ResolvedMapPlace = Awaited<ReturnType<typeof resolveMapPlace>>
export type MapChatAnswer = { text: string; places: MapPlace[]; navigation?: string; warning?: string }

export async function answerMapQuestion(
  question: string, context: MapChatContext, history: MapChatTurn[], signal: AbortSignal,
  onStatus: (status: string) => void,
  onFocus: (target: ResolvedMapPlace) => string,
): Promise<MapChatAnswer> {
  if (!question.trim() || question.length > 3000) throw new Error('Enter a map question of at most 3,000 characters.')
  onStatus('Connecting to Fabric...')
  await ensureMapChatConnection(signal)
  signal.throwIfAborted()
  let places: MapPlace[] = []
  let candidatesTruncated = false
  let navigation: string | undefined
  const search = navigationSearch(question)
  let questionContext = context
  const navigate = async (reference: MapPlaceReference) => {
    const target = await resolveMapPlace(reference, signal)
    signal.throwIfAborted()
    navigation = onFocus(target)
  }
  if (search) {
    onStatus('Finding the place in Fabric...')
    const result = await searchMapPlaces(search, signal, navigationLayer(question))
    places = result.places
    candidatesTruncated = result.truncated
  }
  if (/\b(frequency|frekvens|hertz|hz)\b/i.test(question)) {
    onStatus('Reading the live frequency through Fabric...')
    try {
      questionContext = { ...context, liveFrequency: await queryLiveGridFrequency(signal) }
    } catch (reason) {
      if (signal.aborted) throw reason
      console.error('Map chat could not read live frequency.', reason)
      questionContext = { ...context, liveFrequencyError: reason instanceof Error ? reason.message : 'Live frequency could not be read.' }
    }
  }
  onStatus('The map Data Agent is querying the data...')
  const answer = await askMapDataAgent(mapChatPrompt(question, questionContext, history, places), signal)
  signal.throwIfAborted()
  const parsed = parseMapChatAnswer(answer.text)
  let warning = candidatesTruncated ? 'More places match than are shown. Narrow the name or choose a candidate below.' : parsed.actionError
  if (isMapNavigationRequest(question)) {
    const exact = places.filter(place => place.exactMatch)
    if (exact.length > 1 || (exact.length === 0 && places.length > 1)) {
      warning = 'Several places have that name. Choose the correct asset below.'
    } else {
      const proposed = parsed.focus
      const reference = exact.length === 1 ? { feature_id: exact[0].feature_id, layer_id: exact[0].layer_id }
        : !parsed.actionError && proposed && (!places.length || places.some(place => place.feature_id === proposed.feature_id && place.layer_id === proposed.layer_id))
          ? proposed : undefined
      try {
        if (reference) await navigate(reference)
      } catch (reason) {
        if (signal.aborted) throw reason
        console.error('Map chat navigation could not be verified.', reason)
        warning = reason instanceof Error ? reason.message : 'The requested place could not be verified.'
      }
    }
  }
  return {
    text: parsed.text || 'The map Data Agent returned no answer.',
    places: navigation ? [] : places,
    navigation, warning,
  }
}
