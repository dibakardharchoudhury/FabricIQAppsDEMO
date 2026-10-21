import { useEffect, useRef, useState } from 'react'
import { Bot, MapPin, Send, Square, SquarePen, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CopilotThinking } from '../../../components/CopilotThinking'
import { answerMapQuestion, type ResolvedMapPlace } from '../../../services/mapChat'
import { resolveMapPlace } from '../../../services/energyMap'
import type { MapChatContext, MapChatTurn, MapPlace } from '../../mapChatModel'

type Message = { id: string; role: 'user' | 'assistant'; text: string; places?: MapPlace[]; navigation?: string; warning?: string; error?: boolean }
const welcome = (): Message => ({
  id: crypto.randomUUID(), role: 'assistant',
  text: 'Ask about the imported map data or ask me to show an asset or reservoir area. I use the dedicated Fabric map Data Agent and verify places before moving the map.',
})
const prompts = ['What is the capacity currently shown?', 'Show me Adamselv', 'Which owners have the most installed hydropower capacity?']

export function MapChatDrawer({ open, context, onClose, onFocus }: {
  open: boolean
  context: MapChatContext
  onClose: () => void
  onFocus: (target: ResolvedMapPlace) => string
}) {
  const [messages, setMessages] = useState<Message[]>(() => [welcome()])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const history = useRef<MapChatTurn[]>([])
  const request = useRef<{ controller: AbortController; messageId: string; navigationOnly?: boolean } | null>(null)
  const followBottom = useRef(true)
  const focus = useRef(onFocus)
  useEffect(() => { focus.current = onFocus }, [onFocus])
  useEffect(() => {
    if (open) input.current?.focus()
    return () => { request.current?.controller.abort() }
  }, [open])
  useEffect(() => () => {
    request.current?.controller.abort()
    request.current = null
  }, [])
  useEffect(() => {
    if (list.current && followBottom.current) list.current.scrollTop = list.current.scrollHeight
  }, [messages, status])

  const update = (id: string, patch: Partial<Message>) => setMessages(current =>
    current.map(message => message.id === id ? { ...message, ...patch } : message))
  const stop = () => {
    const active = request.current
    if (!active) return
    active.controller.abort()
    update(active.messageId, active.navigationOnly ? { warning: 'Navigation cancelled.' } : { text: 'Request cancelled.' })
    request.current = null
    setBusy(false); setStatus('')
  }
  const close = () => { stop(); onClose() }
  const send = async (value = question) => {
    const text = value.trim()
    if (!text || request.current) return
    const id = crypto.randomUUID()
    const controller = new AbortController()
    request.current = { controller, messageId: id }
    setQuestion(''); setBusy(true); followBottom.current = true
    setMessages(current => [...current,
      { id: crypto.randomUUID(), role: 'user', text },
      { id, role: 'assistant', text: '' },
    ])
    try {
      const answer = await answerMapQuestion(text, context, history.current, controller.signal,
        value => { if (!controller.signal.aborted) setStatus(value) },
        target => focus.current(target))
      if (controller.signal.aborted || request.current?.controller !== controller) return
      update(id, answer)
      history.current = [...history.current, { question: text, answer: answer.text }].slice(-4)
    } catch (reason) {
      if (request.current?.controller !== controller) return
      if (controller.signal.aborted) update(id, { text: 'Request cancelled.' })
      else {
        console.error('Map chat failed.', reason)
        update(id, { text: reason instanceof Error ? reason.message : 'Map chat is unavailable.', error: true })
      }
    } finally {
      if (request.current?.controller === controller) {
        request.current = null
        setBusy(false); setStatus('')
      }
    }
  }
  const showPlace = async (messageId: string, place: MapPlace) => {
    if (request.current) return
    const controller = new AbortController()
    request.current = { controller, messageId, navigationOnly: true }
    setBusy(true); setStatus('Verifying the selected place in Fabric...')
    try {
      const target = await resolveMapPlace({ feature_id: place.feature_id, layer_id: place.layer_id }, controller.signal)
      controller.signal.throwIfAborted()
      update(messageId, { navigation: focus.current(target), warning: undefined })
    } catch (reason) {
      if (controller.signal.aborted) return
      console.error('Map chat place selection failed.', reason)
      update(messageId, { warning: reason instanceof Error ? reason.message : 'The selected place could not be verified.' })
    } finally {
      if (request.current?.controller === controller) { request.current = null; setBusy(false); setStatus('') }
    }
  }
  return <aside className="energy-map-chat" aria-label="Map chat" hidden={!open} onKeyDown={event => {
    if (event.key === 'Escape') { event.stopPropagation(); close() }
  }}>
    <header><Bot size={19} /><div><h2>Map chat</h2><small>Fabric Data Agent - map data</small></div>
      <button type="button" aria-label="New map chat" disabled={busy} onClick={() => { history.current = []; setMessages([welcome()]) }}><SquarePen size={17} /></button>
      <button type="button" aria-label="Close map chat" onClick={close}><X size={19} /></button>
    </header>
    <p className="energy-map-chat-context">Current map filters and selected asset are included. Answers distinguish the visible subset from the complete imported data.</p>
    <div className="energy-map-chat-messages" ref={list} onScroll={() => {
      if (list.current) followBottom.current = list.current.scrollHeight - list.current.scrollTop - list.current.clientHeight < 48
    }}>
      {messages.map(message => <article key={message.id} className={`energy-map-chat-message ${message.role}${message.error ? ' error' : ''}`}>
        <strong>{message.role === 'user' ? 'You' : 'Map assistant'}</strong>
        {message.text ? message.role === 'assistant'
          ? <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: () => null }}>{message.text}</ReactMarkdown> : <p>{message.text}</p>
          : <CopilotThinking />}
        {message.navigation && <p className="energy-map-chat-navigation"><MapPin size={14} />{message.navigation}</p>}
        {message.warning && <p role="alert" className="energy-map-stale">{message.warning}</p>}
        {message.places?.length ? <div className="energy-map-chat-places">
          <p>Choose a place to show:</p>
          {message.places.map(place => <button key={`${place.layer_id}:${place.feature_id}`} type="button"
            disabled={busy || !place.bounds} onClick={() => void showPlace(message.id, place)}>
            <strong>{place.label}</strong><small>{[place.owner, place.priceArea, place.layer_id, !place.bounds ? 'No verified coordinates' : ''].filter(Boolean).join(' - ')}</small>
          </button>)}
        </div> : null}
      </article>)}
      {messages.length === 1 && <div className="energy-map-chat-suggestions">{prompts.map(prompt =>
        <button type="button" key={prompt} onClick={() => void send(prompt)}>{prompt}</button>)}</div>}
    </div>
    {busy && <div className="energy-map-chat-status" role="status">{status || 'Working...'}<button type="button" onClick={stop}><Square size={12} />Stop</button></div>}
    <form onSubmit={event => { event.preventDefault(); void send() }}>
      <textarea ref={input} aria-label="Ask the map assistant" value={question} maxLength={3000}
        placeholder="Ask about the map, or show me an asset..." onChange={event => setQuestion(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() }
        }} />
      <button type="submit" aria-label="Send map question" disabled={busy || !question.trim()}><Send size={18} /></button>
    </form>
  </aside>
}
