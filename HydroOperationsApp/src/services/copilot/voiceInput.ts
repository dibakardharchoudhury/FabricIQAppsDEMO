export type VoiceResultEvent = {
  resultIndex: number
  results: ArrayLike<{ isFinal: boolean; [index: number]: { transcript: string } }>
}

export interface VoiceRecognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: VoiceResultEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

declare global {
  interface Window {
    SpeechRecognition?: new () => VoiceRecognition
    webkitSpeechRecognition?: new () => VoiceRecognition
  }
}

const ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone permission was denied. Allow microphone access in browser settings or type your question.',
  'service-not-allowed': 'Browser speech recognition is unavailable or blocked by policy. Type your question instead.',
  'audio-capture': 'No usable microphone was found. Check the connected input device.',
  'no-speech': 'No speech was detected. Try dictation again or type your question.',
  network: 'The browser speech service could not be reached. Your typed text is unchanged.',
  aborted: 'Dictation was interrupted. Review the text already captured before sending.',
}

export function startDictation(recognition: VoiceRecognition, callbacks: {
  onTranscript: (text: string) => void
  onInterim: (text: string) => void
  onError: (message: string) => void
  onEnd: () => void
}, language = 'en-GB') {
  let active = true
  let nextResult = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const finish = () => {
    if (!active) return
    active = false
    clearTimeout(timer)
    recognition.onresult = recognition.onerror = recognition.onend = null
    callbacks.onInterim('')
    callbacks.onEnd()
  }
  const cancel = () => {
    if (!active) return
    finish()
    recognition.abort()
  }
  recognition.lang = language
  recognition.continuous = false
  recognition.interimResults = true
  recognition.onresult = event => {
    if (!active) return
    const interim: string[] = []
    for (let index = nextResult; index < event.results.length; index++) {
      const result = event.results[index]
      const text = result[0]?.transcript.trim()
      if (result.isFinal) {
        nextResult = index + 1
        if (text) callbacks.onTranscript(text)
      } else if (text) interim.push(text)
    }
    callbacks.onInterim(interim.join(' '))
  }
  recognition.onerror = event => {
    if (!active) return
    callbacks.onError(ERRORS[event.error] ?? `Dictation failed (${event.error}). Type your question or retry.`)
    cancel()
  }
  recognition.onend = finish
  try {
    recognition.start()
    if (active) timer = setTimeout(() => {
      callbacks.onError('Dictation reached its 60-second limit. Review the captured text; restart to add more.')
      cancel()
    }, 60_000)
  } catch (error) {
    callbacks.onError(error instanceof Error ? error.message : 'Browser dictation could not start.')
    finish()
  }
  return { cancel, stop: () => { if (active) recognition.stop() } }
}
