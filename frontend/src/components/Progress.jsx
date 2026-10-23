import { useEffect, useState } from 'react'
import { pace } from '../hooks.js'
import WarningLabel from './WarningLabel.jsx'

const ICON = { pending: '○', running: '↻', done: '✓', failed: '✕' }

const STAGES = { queued: 'Queued', fetching: 'Fetching accounts, balances and transactions', cleaning: 'Cleaning the data', scoring: 'Scoring', done: 'Done', failed: 'Failed' }

// Keep presentation pacing separate from the unchanged, actual source outcomes below.
export function AnimatedProgress({ job, onFinished }) {
  const items = job.progress || []
  const [step, setStep] = useState(0)
  const currentFinal = step < items.length && ['done', 'failed'].includes(items[step].state)
  const allIn = step >= items.length
  const jobEnded = ['done', 'failed'].includes(job.status)
  useEffect(() => {
    if (!currentFinal) return undefined
    const timer = setTimeout(() => setStep((value) => value + 1), pace(800))
    return () => clearTimeout(timer)
  }, [step, currentFinal])
  useEffect(() => {
    if (!allIn || !jobEnded) return undefined
    const timer = setTimeout(onFinished, pace(700))
    return () => clearTimeout(timer)
  }, [allIn, jobEnded, onFinished])
  return <div>
    <p className="hint">{allIn ? (jobEnded ? 'Scoring' : STAGES[job.stage]) : STAGES.fetching}…</p>
    <ul className="progress">{items.map((item, i) => {
      const state = i < step ? item.state : i === step ? 'running' : 'pending'
      return <li key={item.source} className={state}>
        <span className="icon" aria-hidden="true">{state === 'running' ? <span className="spinner" /> : ICON[state]}</span><span className="label">{item.label}</span><span className="detail">{state === 'failed' && <><WarningLabel />: </>}{state === 'running' ? 'contacting…' : state === 'pending' ? '' : item.detail}</span>
      </li>
    })}</ul>
    <div className="meter" aria-hidden="true"><i style={{ width: `${items.length ? Math.min(step, items.length) / items.length * 100 : 0}%` }} /></div>
  </div>
}

export default function Progress({ job }) {
  return <div>
    <p className="hint">Actual source status. Requests run in parallel; completed events remain available in the case trace.</p>
    <ul className="progress">{(job.progress || []).map((p) => <li key={p.source} className={p.state}>
      <span className="icon" aria-hidden="true">{ICON[p.state]}</span><span className="label">{p.label}</span><span className="detail">{p.state === 'failed' && <><WarningLabel />: </>}{p.detail || p.state}</span>
    </li>)}</ul>
    <ol className="trace-preview">{(job.trace || []).slice(-5).map((event, i) => <li key={i}><span className="mono">{event.elapsed_ms} ms</span> {event.stage}</li>)}</ol>
  </div>
}
