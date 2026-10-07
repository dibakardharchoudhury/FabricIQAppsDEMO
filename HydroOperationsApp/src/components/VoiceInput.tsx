import { useEffect, useRef, useState } from 'react'
import { Mic, Square } from 'lucide-react'
import { startDictation } from '../services/copilot/voiceInput'

export function VoiceInput({ disabled, resetKey, onTranscript }: {
  disabled: boolean
  resetKey: string | number
  onTranscript: (text: string) => void
}) {
  const [notice, setNotice] = useState(false)
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState('')
  const session = useRef<ReturnType<typeof startDictation>>(undefined)
  const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition
  const supported = window.isSecureContext && Boolean(Recognition)

  useEffect(() => () => { session.current?.cancel() }, [disabled, resetKey])

  const start = () => {
    if (disabled || listening || !Recognition || !supported) return
    setError('')
    setListening(true)
    try {
      session.current = startDictation(new Recognition(), {
        onTranscript, onInterim: setInterim, onError: setError, onEnd: () => setListening(false),
      }, document.documentElement.lang || navigator.language || 'en-GB')
    } catch (cause) {
      setListening(false)
      setError(cause instanceof Error ? cause.message : 'Browser dictation could not start.')
    }
  }
  return <div className="voice-input">
    <button type="button" className={listening ? 'listening' : ''} disabled={disabled}
      aria-label={listening ? 'Stop dictation' : 'Voice input'} aria-pressed={listening}
      onClick={() => listening ? session.current?.stop() : setNotice(value => !value)}>
      {listening ? <Square size={16} /> : <Mic size={16} />}
    </button>
    {(notice || listening || error) && <div className="voice-input-notice">
      {notice && <p>{supported
        ? 'Browser dictation may send audio to your browser vendor for speech recognition. Microphone permission is required. Review the transcript, then press Send. Voice never approves work orders.'
        : 'Voice input is not supported in this browser or embedded page. Use a supported browser over HTTPS with microphone access, or type your question.'}</p>}
      {listening && <p className="voice-input-status" role="status">{interim ? `Hearing: ${interim}` : 'Listening... Speak your question.'}</p>}
      {error && <p className="voice-input-error" role="alert">{error}</p>}
      {supported && !listening && <button type="button" disabled={disabled} onClick={start}>Start browser dictation</button>}
    </div>}
  </div>
}
