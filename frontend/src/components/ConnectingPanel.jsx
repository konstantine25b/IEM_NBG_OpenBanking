import { useEffect, useState } from 'react'
import { pace } from '../hooks.js'

const ICON = { pending: '○', running: null, done: '✓', skipped: '–', failed: '✕' }
const FIRST_MS = 600 // first answer
const GAP_MS = 190 // spacing between answers
const DONE_MS = 800 // pause on the finished list before it folds into the table

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

// What a source answered, as a row state and a text.
function outcome(source, result) {
  const entry = source.kind === 'bank' ? result.banks.find((b) => b.bank === source.key) : result[source.key]
  if (!entry) return { state: 'failed', text: 'no answer' }
  if (entry.status === 'connected') {
    return { state: 'done', text: source.kind === 'bank' ? `Consent valid · ${plural(entry.accounts, 'account')}` : 'Consent valid' }
  }
  if (entry.status === 'no_relationship') return { state: 'skipped', text: 'No account · left out' }
  return { state: 'failed', text: entry.message || 'Error' }
}

// All requests go out at once (every row spins), then the answers come back one after another.
// The API replies in a blink, so the answers are paced; `result` is null until the real reply has arrived.
export default function ConnectingPanel({ sources, result, onDone }) {
  const [ready, setReady] = useState({})

  useEffect(() => {
    // a fixed shuffle (5 is coprime with 14) so the order looks like uneven network latency
    const timers = sources.map((s, i) =>
      setTimeout(() => setReady((r) => ({ ...r, [s.key]: true })), pace(FIRST_MS + ((i * 5) % sources.length) * GAP_MS)),
    )
    return () => timers.forEach(clearTimeout)
  }, [sources])

  const answered = result ? sources.filter((s) => ready[s.key]).length : 0
  const finished = result && answered === sources.length

  useEffect(() => {
    if (!finished) return undefined
    const id = setTimeout(onDone, pace(DONE_MS))
    return () => clearTimeout(id)
  }, [finished, onDone])

  return (
    <div>
      <p className="hint">
        Sending consent requests to {sources.filter((s) => s.kind === 'bank').length} banks, the Revenue Service and Creditinfo…
        {' '}{answered} of {sources.length} answered
      </p>
      <ul className="progress sources">
        {sources.map((s) => {
          const o = result && ready[s.key] ? outcome(s, result) : { state: 'running', text: 'requesting consent…' }
          return (
            <li key={s.key} className={o.state}>
              <span className="icon" aria-hidden="true">{ICON[o.state] ?? <span className="spinner" />}</span>
              <span className="label">{s.label}</span>
              <span className="detail">{o.text}</span>
            </li>
          )
        })}
      </ul>
      <div className="meter" aria-hidden="true">
        <i style={{ width: `${(answered / sources.length) * 100}%` }} />
      </div>
    </div>
  )
}
