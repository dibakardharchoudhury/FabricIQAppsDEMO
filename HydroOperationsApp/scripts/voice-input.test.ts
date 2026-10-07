import assert from 'node:assert/strict'
import test from 'node:test'
import { startDictation, type VoiceRecognition } from '../src/services/copilot/voiceInput.ts'

function fixture() {
  let starts = 0, stops = 0, aborts = 0, ends = 0
  const transcripts: string[] = [], interim: string[] = [], errors: string[] = []
  const recognition: VoiceRecognition = {
    lang: '', continuous: true, interimResults: false, onresult: null, onerror: null, onend: null,
    start() { starts++ }, stop() { stops++ }, abort() { aborts++ },
  }
  const callbacks = { onTranscript: (text: string) => transcripts.push(text), onInterim: (text: string) => interim.push(text),
    onError: (message: string) => errors.push(message), onEnd: () => { ends++ } }
  return { recognition, callbacks, transcripts, interim, errors, counts: () => ({ starts, stops, aborts, ends }) }
}

test('dictation emits only final transcripts, once, and never sends or approves anything', () => {
  const f = fixture()
  const session = startDictation(f.recognition, f.callbacks)
  try {
    assert.equal(f.recognition.lang, 'en-GB')
    assert.equal(f.recognition.continuous, false)
    f.recognition.onresult!({ resultIndex: 0, results: [{ isFinal: false, 0: { transcript: ' Which turbines' } }] })
    assert.deepEqual(f.transcripts, [])
    assert.equal(f.interim.at(-1), 'Which turbines')
    const final = { resultIndex: 0, results: [{ isFinal: true, 0: { transcript: ' Which turbines are hot? ' } }] }
    f.recognition.onresult!(final)
    f.recognition.onresult!(final)
    assert.deepEqual(f.transcripts, ['Which turbines are hot?'])
    session.stop()
    assert.equal(f.counts().stops, 1)
    f.recognition.onend!()
    assert.equal(f.counts().ends, 1)
    assert.equal(f.recognition.onresult, null)
  } finally { session.cancel() }
})

test('cancelling on send/reset/unmount discards late callbacks', () => {
  const f = fixture()
  const session = startDictation(f.recognition, f.callbacks)
  const late = f.recognition.onresult!
  session.cancel()
  session.cancel()
  late({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: 'late text' } }] })
  assert.deepEqual(f.transcripts, [])
  assert.deepEqual(f.counts(), { starts: 1, stops: 0, aborts: 1, ends: 1 })
})

test('microphone denial, service errors and synchronous startup errors are explicit', () => {
  for (const code of ['not-allowed', 'service-not-allowed', 'audio-capture', 'network', 'no-speech', 'unknown']) {
    const f = fixture()
    const session = startDictation(f.recognition, f.callbacks)
    f.recognition.onerror!({ error: code })
    assert.equal(f.errors.length, 1)
    assert.equal(f.counts().ends, 1)
    assert.deepEqual(f.transcripts, [])
    session.cancel()
  }
  const f = fixture()
  f.recognition.start = () => { throw new Error('Permission policy blocked microphone') }
  startDictation(f.recognition, f.callbacks).cancel()
  assert.deepEqual(f.errors, ['Permission policy blocked microphone'])
  assert.equal(f.counts().ends, 1)
})
