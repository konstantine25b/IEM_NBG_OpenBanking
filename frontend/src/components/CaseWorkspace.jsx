import { useEffect, useState } from 'react'
import { finishBenchmark, getAudit, getCase, reviewCase, startBenchmark } from '../api.js'
import Result from './Result.jsx'
import Progress from './Progress.jsx'

const money = (n) => n == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
const decisions = { accepted: 'Estimate accepted', more_evidence: 'More evidence requested', rejected: 'Estimate rejected' }

function Statement({ transactions, annotated = true }) {
  const [filter, setFilter] = useState(annotated ? 'service' : 'all')
  const [page, setPage] = useState(0)
  const filtered = transactions.filter((row) => filter === 'all' || (filter === 'service' ? row.classification === 'Service payment' : filter === 'excluded' ? !row.retained : row.retained))
  const pages = Math.max(1, Math.ceil(filtered.length / 25))
  return <div>
    <div className="toolbar">{annotated && <label>Show <select value={filter} onChange={(e) => { setFilter(e.target.value); setPage(0) }}><option value="service">Service payments</option><option value="retained">Retained activity</option><option value="excluded">Excluded activity</option><option value="all">All records</option></select></label>}<span className="hint">{filtered.length} records · identifiers masked</span></div>
    <div className="table-scroll"><table><thead><tr><th>Date / bank</th><th>Description / reference</th><th>Counterparty</th><th className="num-col">Amount</th>{annotated && <th>Assessment</th>}</tr></thead>
      <tbody>{filtered.slice(page * 25, (page + 1) * 25).map((row, i) => <tr key={`${row.reference}-${i}`}><td>{row.date}<br /><small>{row.bank}</small></td><td>{row.description}<br /><span className="mono">{row.reference}</span></td><td className="mono">{row.counterparty}</td><td className="num-col">{money(row.amount)} {row.currency}</td>{annotated && <td>{row.classification}<br /><small>{row.exclusion_reason}</small></td>}</tr>)}</tbody>
    </table></div>
    <div className="toolbar"><button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} / {pages}</span><button className="secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
  </div>
}

function Evidence({ evidence }) {
  const service = evidence.service_payments
  const baseline = evidence.salary_baseline
  return <section className="card"><h2>Underlying transaction evidence</h2>
    <dl className="facts"><div><dt>Service payments</dt><dd>{service.count}</dd></div><div><dt>Total service receipts</dt><dd>{money(service.total_gel)} GEL</dd></div><div><dt>Receipts / {evidence.window.months} months</dt><dd>{money(service.monthly_gel)} GEL / month</dd></div><div><dt>Observation window</dt><dd>{evidence.window.from} – {evidence.window.to}</dd></div></dl>
    <p className="hint">{evidence.rules}</p>
    <div className="notice warn baseline"><strong>Salary-only baseline: {baseline.status === 'abstains' ? 'cannot estimate income' : `${money(baseline.monthly_gel)} GEL / month`}</strong><p>{baseline.message} {baseline.count} salary credits identified. Zero salary credits does not establish zero income or zero officially declared salary.</p></div>
    <details><summary>Service receipts by calendar month</summary><table><thead><tr><th>Month</th><th className="num-col">Payments</th><th className="num-col">GEL</th></tr></thead><tbody>{Object.entries(service.monthly).map(([month, value]) => <tr key={month}><td>{month}</td><td className="num-col">{value.count}</td><td className="num-col">{money(value.total_gel)}</td></tr>)}</tbody></table><p className="hint">Boundary months are partial. Monthly receipts divide the total by the observation-window length.</p></details>
    <details open><summary>Inspect transactions and exclusion reasons</summary><Statement transactions={evidence.transactions} /></details>
  </section>
}

function Trace({ job }) {
  const events = job.trace || []
  const stages = events.filter((e) => e.stage !== 'Bank API call')
  const calls = events.filter((e) => e.stage === 'Bank API call')
  return <section className="card"><h2>Measured processing evidence</h2>
    <dl className="facts"><div><dt>Backend processing</dt><dd>{money(job.processing_ms)} ms</dd></div><div><dt>Recorded bank API calls</dt><dd>{calls.length}</dd></div></dl>
    <p className="hint">Backend timestamps and wall-clock durations; presentation animation and browser polling are excluded. Sources run in parallel.</p>
    <details open><summary>Execution trace</summary><div className="table-scroll"><table><thead><tr><th>Elapsed</th><th>Event</th><th>Evidence</th></tr></thead><tbody>{stages.map((event, i) => <tr key={i}><td className="mono">{event.elapsed_ms} ms</td><td>{event.stage}</td><td className="mono trace-detail">{event.stage === 'Case created' ? `Scenario: ${event.detail.scenario}; ${event.detail.consents.sources.length} consent outcomes; model ${event.detail.model_sha256.slice(0, 12)}…` : JSON.stringify(event.detail)}</td></tr>)}</tbody></table></div></details>
    <details><summary>Individual bank API responses</summary><div className="table-scroll"><table><thead><tr><th>Source</th><th>Request</th><th>Status</th><th>Duration</th></tr></thead><tbody>{calls.map(({ detail }, i) => <tr key={i}><td>{detail.source}</td><td className="mono">{detail.method} {detail.route}</td><td>{detail.http_status}</td><td>{detail.duration_ms} ms</td></tr>)}</tbody></table></div></details>
  </section>
}

function ManualBenchmark({ task, caseId, onDone, onCancel }) {
  const [count, setCount] = useState('')
  const [total, setTotal] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => { const timer = setInterval(() => setElapsed(Math.max(0, Math.floor((Date.now() - Date.parse(task.started_at)) / 1000))), 1000); return () => clearInterval(timer) }, [task.started_at])
  const finish = async (event) => {
    event.preventDefault(); setBusy(true); setError(null)
    try { await finishBenchmark(caseId, Number(count), Number(total)); await onDone() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section className="card"><h2>Manual aggregation task · {elapsed} seconds</h2><p>{task.task}</p><p className="hint">Use the statement and your usual calculation tools. A correct answer is required to stop the server timer. This is a self-timed prototype comparison.</p>
    <form className="review-form" onSubmit={finish}><label>Service-payment count<input type="number" min="0" required value={count} onChange={(e) => setCount(e.target.value)} /></label><label>Total receipts in GEL<input type="number" min="0" step="0.01" required value={total} onChange={(e) => setTotal(e.target.value)} /></label>{error && <p className="error" role="alert">{error}</p>}<div className="toolbar"><button disabled={busy}>Submit and stop timer</button><button type="button" className="secondary" onClick={onCancel}>Leave task</button></div></form>
    <Statement transactions={task.transactions} annotated={false} />
  </section>
}

function Review({ job, user, refresh }) {
  const [decision, setDecision] = useState('more_evidence')
  const [reason, setReason] = useState('')
  const [ack, setAck] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const save = async (event) => {
    event.preventDefault(); setBusy(true); setError(null)
    try { await reviewCase(job.job_id, decision, reason, ack); await refresh() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const uncertain = ['low', 'very low'].includes(job.result.confidence.level) || job.result.partial_sources
  return <section className="card"><h2>Human review of the income estimate</h2><p>Every estimate requires an officer decision. This prototype does not approve loans.</p>
    {job.review && <div className="notice warn"><strong>{decisions[job.review.decision]}</strong><p>{job.review.reason}</p><small>{job.review.reviewer} · {new Date(job.review.at).toLocaleString()}</small></div>}
    {user.role === 'officer' ? <form className="review-form" onSubmit={save}><label>Decision<select value={decision} onChange={(e) => setDecision(e.target.value)}><option value="more_evidence">Request more evidence</option><option value="accepted" disabled={!job.result.income}>Accept income estimate</option><option value="rejected">Reject income estimate</option></select></label><label>Review rationale<textarea required minLength={10} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Record the evidence and limitations behind your decision" /></label>
      {uncertain && <label className="checkbox-label"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />I have reviewed the low confidence or missing-source limitation.</label>}
      {error && <p className="error" role="alert">{error}</p>}<button disabled={busy || reason.trim().length < 10 || (decision === 'accepted' && uncertain && !ack)}>Record officer review</button>
    </form> : <p className="hint">Auditor access is read-only. Only an officer can record a decision.</p>}
  </section>
}

export default function CaseWorkspace({ initialJob, user, onUpdated }) {
  const [job, setJob] = useState(initialJob)
  const [audit, setAudit] = useState(null)
  const [task, setTask] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => { getAudit(initialJob.job_id).then(setAudit).catch((e) => setError(e.message)) }, [initialJob.job_id])
  const refresh = async () => { const [saved, trail] = await Promise.all([getCase(job.job_id), getAudit(job.job_id)]); setJob(saved); setAudit(trail); onUpdated() }
  const begin = async () => { setError(null); try { setTask(await startBenchmark(job.job_id)) } catch (e) { setError(e.message) } }
  if (task) return <ManualBenchmark task={task} caseId={job.job_id} onCancel={() => setTask(null)} onDone={async () => { setTask(null); await refresh() }} />
  return <div className="case-workspace">
    <section className="card case-heading"><h2>Case {job.job_id} · {job.subject}</h2><span className={`pill ${job.review ? 'ok' : 'warn'}`}>{job.review ? decisions[job.review.decision] : 'Officer review required'}</span><p className="hint">Identifiers masked in evidence and exports. Case results and audit events are saved locally.</p>{job.scenario !== 'none' && <p className="notice warn">Explicit fault simulation: {job.scenario === 'rs_unavailable' ? 'Revenue Service returns HTTP 503' : 'Connected banks return HTTP 503'}.</p>}</section>
    {error && <p className="error" role="alert">{error}</p>}
    <section className="card"><h3>Source outcomes</h3><Progress job={job} /><details><summary>Consent scope and source authorization</summary><p className="hint">{job.consent_summary.mode}</p><p>{job.consent_summary.scope.join(' · ')}</p><ul>{job.consent_summary.sources.map((s) => <li key={s.source}>{s.source}: {s.status} · consent {s.consent}</li>)}</ul></details></section>
    <Result result={job.result} /><Evidence evidence={job.result.evidence} /><Trace job={job} /><Review job={job} user={user} refresh={refresh} />
    <section className="card"><h2>Measured manual-task comparison</h2>{job.benchmark ? <><dl className="facts"><div><dt>Operator aggregation</dt><dd>{money(job.benchmark.manual_ms / 1000)} seconds</dd></div><div><dt>Automated evidence aggregation</dt><dd>{money(job.benchmark.automated_ms)} ms</dd></div><div><dt>Difference (manual − automated)</dt><dd>{money((job.benchmark.manual_ms - job.benchmark.automated_ms) / 1000)} seconds</dd></div></dl><p className="hint">{job.benchmark.limitation} Recorded {new Date(job.benchmark.completed_at).toLocaleString()}.</p></> : <p>No manual measurement recorded. Processing time alone does not establish time saved.</p>}{user.role === 'officer' && <button className="secondary" onClick={begin} disabled={!job.result.evidence.transactions.length}>Start a measured manual aggregation task</button>}</section>
    <section className="card"><h2>Persistent audit trail</h2><div className="toolbar"><span className={`pill ${audit?.chain_valid && audit?.case_matches_audit ? 'ok' : 'bad'}`}>{audit?.chain_valid && audit?.case_matches_audit ? 'Hash chain and case result verified' : 'Audit unavailable or invalid'}</span><a className="button-link" href={`/api/v1/cases/${job.job_id}/export`} download>Download case and audit JSON</a></div><p className="hint">{audit?.limitation} Source tokens, full personal identifiers and bank credentials are not exported.</p>
      <details><summary>{audit?.events.length || 0} recorded events</summary><div className="table-scroll"><table><thead><tr><th>UTC timestamp</th><th>Actor</th><th>Event</th></tr></thead><tbody>{audit?.events.map((e) => <tr key={e.event_hash}><td className="mono">{e.at}</td><td>{e.actor}</td><td>{e.kind}</td></tr>)}</tbody></table></div></details><p className="hint mono">Model SHA-256: {job.model_sha256}<br />Evidence SHA-256: {job.result.evidence.snapshot_sha256}</p>
    </section>
  </div>
}
