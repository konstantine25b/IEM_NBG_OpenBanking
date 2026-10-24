import { useState } from 'react'
import { reviewCase } from '../api.js'
import WarningLabel from './WarningLabel.jsx'

const LABELS = { accept: 'Accepted', request_more_evidence: 'More documents requested', reject: 'Rejected' }
const MIN_REASON = 10

export function when(at) {
  const date = at ? new Date(at) : null
  return date && !Number.isNaN(date.getTime()) ? `${date.toISOString().slice(0, 16).replace('T', ' ')} UTC` : 'time not recorded'
}

export function DecisionRecord({ review }) {
  return <p className="decision-record">
    <strong>Decision: {LABELS[review.decision] ?? review.decision}</strong> by {review.officer} · {when(review.at)}
    {review.reason && <> · “{review.reason}”</>}
  </p>
}

// The model suggests, the officer decides. High confidence needs one click; anything shakier needs a reason and an
// explicit acknowledgement (the API enforces the same rules and writes the decision to the hash-chained audit).
export default function OfficerDecision({ caseId, result, review, onDecided }) {
  const [reason, setReason] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const withheld = !result.income
  const strict = withheld || !['high', 'very high'].includes(result.confidence.level) || result.partial_sources
  const missing = reason.trim().length < MIN_REASON ? `Write a reason of at least ${MIN_REASON} characters first.` : !acknowledged ? 'Tick the box to confirm you reviewed the uncertainty and the evidence.' : null

  const decide = async (decision) => {
    if (strict && missing) { setError(missing); return }
    setBusy(true); setError(null)
    try { onDecided(await reviewCase(caseId, { decision, reason: reason.trim(), acknowledged_uncertainty: acknowledged })) }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  return <section className="card officer">
    <h3>Officer decision</h3>
    <p className="hint"><strong>The model suggests, the officer decides.</strong> This records a decision on the income estimate; it is not a loan approval.</p>
    {review ? <>
      <DecisionRecord review={review} />
      <p className="hint">Locked: a case is decided once. The decision is in the audit trail below.</p>
    </> : <div className="decision-form">
      {strict && <div className="notice warn"><WarningLabel />: Double-check before deciding.{' '}
        {withheld ? 'No estimate was produced.' : result.partial_sources ? 'A source failed, so the evidence is incomplete.' : `Confidence is ${result.confidence.level}.`}{' '}
        Write down why, and confirm you reviewed the uncertainty and the evidence.</div>}
      <label>{strict ? 'Reason (required, at least 10 characters)' : 'Reason (optional)'}
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="What did you check?" />
      </label>
      {strict && <label className="checkbox-label"><input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
        I reviewed the uncertainty and the evidence</label>}
      <div className="toolbar">
        {strict ? <>
          {!withheld && <button disabled={busy} onClick={() => decide('accept')}>Accept estimate</button>}
          <button className={withheld ? '' : 'secondary'} disabled={busy} onClick={() => decide('request_more_evidence')}>Request more documents</button>
          <button className="secondary" disabled={busy} onClick={() => decide('reject')}>Reject</button>
        </> : <button disabled={busy} onClick={() => decide('accept')}>Accept estimate</button>}
      </div>
      {error && <p className="error" role="alert"><WarningLabel />: {error}</p>}
    </div>}
  </section>
}
