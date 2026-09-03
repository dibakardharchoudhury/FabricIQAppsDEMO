import { useState } from 'react'
import { ChevronLeft, ChevronRight, Radio } from 'lucide-react'

const LANE_COUNT = 5

export function CopilotThinking() {
  const [helperLane, setHelperLane] = useState(0)
  const [signalLane, setSignalLane] = useState(3)
  const [score, setScore] = useState(0)

  const moveToLane = (lane: number) => {
    const next = Math.max(0, Math.min(LANE_COUNT - 1, lane))
    setHelperLane(next)
    if (next === signalLane) {
      setScore(value => value + 1)
      setSignalLane((next + 1 + score % (LANE_COUNT - 1)) % LANE_COUNT)
    }
  }

  const move = (direction: -1 | 1) => {
    moveToLane(helperLane + direction)
  }

  return <span className="copilot-thinking">
    <span className="copilot-sprint" aria-label={`Signal Sprint. ${score} signals collected.`}>
      <span
        className="copilot-helper-stage"
        role="group"
        tabIndex={0}
        aria-label="Signal Sprint game board. Click or tap a lane to move."
        onPointerDown={event => {
          const bounds = event.currentTarget.getBoundingClientRect()
          const lane = Math.min(LANE_COUNT - 1, Math.floor((event.clientX - bounds.left) / bounds.width * LANE_COUNT))
          moveToLane(lane)
          event.currentTarget.focus()
        }}
        onKeyDown={event => {
          if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1) }
          if (event.key === 'ArrowRight') { event.preventDefault(); move(1) }
        }}
      >
        <span className="copilot-signal" style={{ left: `${signalLane * 20 + 7}%` }} aria-hidden="true"><Radio size={13} /></span>
        <span className="copilot-helper" style={{ left: `${helperLane * 20 + 3}%` }} aria-hidden="true">
        <i className="copilot-helper-antenna" />
        <i className="copilot-helper-visor" />
        <i className="copilot-helper-mouth" />
        <i className="copilot-helper-arm left" />
        <i className="copilot-helper-arm right" />
        <i className="copilot-helper-leg left" />
        <i className="copilot-helper-leg right" />
        </span>
      </span>
      <span className="copilot-sprint-controls">
        <button type="button" onClick={() => move(-1)} disabled={helperLane === 0} title="Move left" aria-label="Move helper left"><ChevronLeft size={14} /></button>
        <span aria-live="polite"><Radio size={11} />{score}</span>
        <button type="button" onClick={() => move(1)} disabled={helperLane === LANE_COUNT - 1} title="Move right" aria-label="Move helper right"><ChevronRight size={14} /></button>
      </span>
    </span>
    <span className="copilot-thinking-copy" role="status" aria-live="polite">
      <span className="copilot-thinking-label">Thinking hard</span>
      <span className="copilot-thinking-dots" aria-hidden="true"><i /><i /><i /></span>
    </span>
  </span>
}

export function CopilotStreamCursor() {
  return <span className="copilot-stream-cursor" aria-hidden="true" />
}