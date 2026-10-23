import { useEffect, useState } from 'react'
import { pace } from '../hooks.js'
import WarningLabel from './WarningLabel.jsx'

const ICON = { pending: '○', running: null, done: '✓', skipped: '–', failed: '✕' }
const FIRST_MS = 600
const GAP_MS = 190
const DONE_MS = 800

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

function outcome(source, result) {
  const entry = source.kind === 'bank' ? result.banks.find((b) => b.bank === source.key) : result[source.key]
  if (!entry) return { state: 'failed', text: 'no answer' }
  if (entry.status === 'connected') {
    return { state: 'done', text: source.kind === 'bank' ? `Consent valid · ${plural(entry.accounts, 'account')}` : 'Consent valid' }
  }
  if (entry.status === 'no_relationship') return { state: 'skipped', text: 'No account · left out' }
  return { state: 'failed', text: entry.message || 'Error' }
}

// Reveal actual consent outcomes at the original demo pace; requests still run in parallel.
export default function ConnectingPanel({ sources, result, onDone }) {
  const [ready, setReady] = useState({})
  useEffect(() => {
    const timers = sources.map((source, i) => setTimeout(
      () => setReady((current) => ({ ...current, [source.key]: true })),
      pace(FIRST_MS + ((i * 5) % sources.length) * GAP_MS),
    ))
    return () => timers.forEach(clearTimeout)
  }, [sources])
  const answered = result ? sources.filter((source) => ready[source.key]).length : 0
  const finished = result && answered === sources.length
  useEffect(() => {
    if (!finished) return undefined
    const timer = setTimeout(onDone, pace(DONE_MS))
    return () => clearTimeout(timer)
  }, [finished, onDone])
  return <div>
    <p className="hint">Sending simulated consent requests to {sources.filter((source) => source.kind === 'bank').length} banks, the Revenue Service and Creditinfo… {answered} of {sources.length} answered</p>
    <ul className="progress sources">{sources.map((source) => {
      const answer = result && ready[source.key] ? outcome(source, result) : { state: 'running', text: 'requesting consent…' }
      return <li key={source.key} className={answer.state}>
        <span className="icon" aria-hidden="true">{ICON[answer.state] ?? <span className="spinner" />}</span><span className="label">{source.label}</span><span className="detail">{answer.state === 'failed' && <><WarningLabel />: </>}{answer.text}</span>
      </li>
    })}</ul>
    <div className="meter" aria-hidden="true"><i style={{ width: `${sources.length ? answered / sources.length * 100 : 0}%` }} /></div>
  </div>
}
