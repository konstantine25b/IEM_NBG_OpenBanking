import { useEffect, useState } from 'react'
import { pace } from '../hooks.js'

const ICON = { pending: '○', running: null, done: '✓', failed: '✕' }
const STAGES = {
  queued: 'Queued',
  fetching: 'Fetching accounts, balances and transactions',
  cleaning: 'Cleaning the data',
  scoring: 'Scoring',
  done: 'Done',
  failed: 'Failed',
}
const STEP_MS = 800 // every source stays "running" at least this long
const FINISH_MS = 700

const isFinal = (step) => step.state === 'done' || step.state === 'failed'

// The servers finish in well under a second. The sources are played back one at a time so that a person can follow.
export default function Progress({ job, onFinished }) {
  const items = job.progress
  const [step, setStep] = useState(0)
  const currentFinal = step < items.length && isFinal(items[step])
  const allIn = items.length > 0 && step >= items.length
  const jobEnded = job.status === 'done' || job.status === 'failed'

  useEffect(() => {
    if (!currentFinal) return undefined
    const id = setTimeout(() => setStep((s) => s + 1), pace(STEP_MS))
    return () => clearTimeout(id)
  }, [step, currentFinal])

  useEffect(() => {
    if (!allIn || !jobEnded) return undefined
    const id = setTimeout(onFinished, pace(FINISH_MS))
    return () => clearTimeout(id)
  }, [allIn, jobEnded, onFinished])

  const shown = (item, i) => (i < step ? item.state : i === step ? 'running' : 'pending')
  const stage = allIn ? (jobEnded ? 'Scoring' : STAGES[job.stage]) : STAGES.fetching

  return (
    <div>
      <p className="hint">{stage}…</p>
      <ul className="progress">
        {items.map((p, i) => {
          const state = shown(p, i)
          return (
            <li key={p.source} className={state}>
              <span className="icon" aria-hidden="true">{ICON[state] ?? <span className="spinner" />}</span>
              <span className="label">{p.label}</span>
              <span className="detail">{state === 'running' ? 'contacting…' : state === 'pending' ? '' : p.detail}</span>
            </li>
          )
        })}
      </ul>
      <div className="meter" aria-hidden="true">
        <i style={{ width: `${items.length ? (Math.min(step, items.length) / items.length) * 100 : 0}%` }} />
      </div>
    </div>
  )
}
