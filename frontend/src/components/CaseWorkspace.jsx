import { useEffect, useState } from 'react'
import { getAudit } from '../api.js'
import Result from './Result.jsx'
import Progress from './Progress.jsx'
import WarningLabel from './WarningLabel.jsx'

const money = (n) => n == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)

function RegistryRecords({ source, registries }) {
  const label = source === 'rs' ? 'Revenue Service' : 'Creditinfo'
  if (!registries) return <p className="hint"><WarningLabel />: Registry evidence was not saved for this case. Run another assessment to inspect these source records.</p>
  const data = registries[source]
  if (!data) return <p className="hint"><WarningLabel />: No {label} records were collected for this assessment. See Source outcomes for details.</p>
  const records = source === 'rs' ? [
    ...(data.incomeItems || []).map((item) => ({ reference: item.code, description: item.description, value: item.amount, unit: item.currency })),
    { reference: 'legalForm', description: 'Taxpayer legal form', value: data.taxpayer?.legalForm },
    { reference: 'geoCitizen', description: 'Georgian citizen', value: data.taxpayer?.geoCitizen == null ? null : data.taxpayer.geoCitizen ? 'Yes' : 'No' },
  ] : [
    ...Object.entries(data.inquiries || {}).map(([key, value]) => ({ reference: key, description: `Credit inquiries in the last ${key.match(/\d+/)?.[0]} month(s)`, value, unit: 'inquiries' })),
    ...Object.entries(data.contracts || {}).map(([key, value]) => ({ reference: key, description: key === 'existingAsDebtor' ? 'Existing contracts as debtor' : 'Terminated contracts as debtor', value, unit: 'contracts' })),
  ]
  return <div>
    <p className="hint">{label} returns registry records rather than bank transactions. {source === 'rs' ? 'SOI04 reports remittance income; the mock supplies no salary declaration or payment dates.' : 'Inquiry and contract counts are credit history, not income payments.'}</p>
    <p className="hint">{records.length} records · identifiers masked</p>
    <div className="table-scroll"><table><thead><tr><th>Reference</th><th>Record</th><th className="num-col">Value</th><th>Unit</th></tr></thead><tbody>{records.map((row) => <tr key={row.reference}><td className="mono">{row.reference}</td><td>{row.description}</td><td className="num-col">{row.value == null ? 'Not reported' : typeof row.value === 'number' ? money(row.value) : row.value}</td><td>{row.unit || '—'}</td></tr>)}</tbody></table></div>
  </div>
}

function Statement({ transactions, registries, annotated = true }) {
  const [filter, setFilter] = useState('all')
  const [source, setSource] = useState('openbanking')
  const [page, setPage] = useState(0)
  const registrySelected = source !== 'openbanking'
  const filtered = transactions.filter((row) => filter === 'all' || (filter === 'service' ? row.classification === 'Service payment' : filter === 'excluded' ? !row.retained : row.retained))
  const pages = Math.max(1, Math.ceil(filtered.length / 25))
  return <div>
    <div className="toolbar"><label>Source<select aria-label="Evidence source" value={source} onChange={(e) => { setSource(e.target.value); setPage(0) }}><option value="openbanking">Open banking</option><option value="rs">Revenue Service (RS)</option><option value="creditinfo">Creditinfo</option></select></label>{annotated && <label>Show <select aria-label="Activity filter" value={filter} disabled={registrySelected} onChange={(e) => { setFilter(e.target.value); setPage(0) }}><option value="service">Service payments</option><option value="retained">Retained activity</option><option value="excluded">Excluded activity</option><option value="all">All records</option></select></label>}{!registrySelected && <span className="hint">{filtered.length} records · identifiers masked</span>}</div>
    {registrySelected ? <RegistryRecords source={source} registries={registries} /> : <>
    <div className="table-scroll"><table><thead><tr><th>Date / bank</th><th>Description / reference</th><th>Counterparty</th><th className="num-col">Amount</th>{annotated && <th>Assessment</th>}</tr></thead>
      <tbody>{filtered.slice(page * 25, (page + 1) * 25).map((row, i) => <tr key={`${row.reference}-${i}`}><td>{row.date}<br /><small>{row.bank}</small></td><td>{row.description}<br /><span className="mono">{row.reference}</span></td><td className="mono">{row.counterparty}</td><td className="num-col">{money(row.amount)} {row.currency}</td>{annotated && <td>{row.classification}<br /><small>{row.exclusion_reason}</small></td>}</tr>)}</tbody>
    </table></div>
    <div className="toolbar"><button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} / {pages}</span><button className="secondary" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    </>}
  </div>
}

function Evidence({ evidence }) {
  const service = evidence.service_payments
  return <section className="card"><h2>Underlying transaction evidence</h2>
    {service.count > 0 && <><dl className="facts"><div><dt>Service payments</dt><dd>{service.count}</dd></div><div><dt>Total service receipts</dt><dd>{money(service.total_gel)} GEL</dd></div><div><dt>Receipts / {evidence.window.months} months</dt><dd>{money(service.monthly_gel)} GEL / month</dd></div><div><dt>Observation window</dt><dd>{evidence.window.from} – {evidence.window.to}</dd></div></dl>
    <p className="hint">{evidence.rules}</p></>}
    <details open><summary>Inspect transactions and exclusion reasons</summary><Statement transactions={evidence.transactions} registries={evidence.registries} /></details>
  </section>
}

function EventTime({ at }) {
  const date = at ? new Date(at) : null
  if (!date || Number.isNaN(date.getTime())) return <span className="hint">Not recorded</span>
  const iso = date.toISOString()
  return <time dateTime={iso} title={iso}>{iso.slice(0, 10)}<br />{iso.slice(11, -1)} UTC</time>
}

function ProcessingAudit({ job, audit }) {
  const events = audit?.events ? audit.events.map((event) => {
    const trace = event.detail?.stage ? event.detail : null
    return { at: trace?.at || event.at, elapsed_ms: trace?.elapsed_ms, stage: event.kind,
      detail: trace?.detail || event.detail, actor: event.actor, key: event.event_hash }
  }) : (job.trace || []).map((event, i) => ({ ...event, actor: 'system', key: i }))
  const stages = events.filter((e) => e.stage !== 'Bank API call')
  const calls = events.filter((e) => e.stage === 'Bank API call')
  const verified = audit?.chain_valid && audit?.case_matches_audit
  return <section className="card"><h2>Processing evidence and audit trail</h2>
    <div className="toolbar"><span className={`pill ${verified ? 'ok' : 'bad'}`}>{verified ? 'Hash chain and case result verified' : <><WarningLabel />: Audit unavailable or invalid</>}</span></div>
    <dl className="facts"><div><dt>Started (UTC)</dt><dd className="mono"><EventTime at={events.find((event) => event.stage === 'Case created')?.at} /></dd></div><div><dt>Completed (UTC)</dt><dd className="mono"><EventTime at={events.find((event) => ['Estimate completed', 'Estimate withheld'].includes(event.stage))?.at} /></dd></div><div><dt>Backend processing</dt><dd>{money(job.processing_ms)} ms</dd></div><div><dt>Recorded bank API calls</dt><dd>{calls.length}</dd></div><div><dt>Recorded events</dt><dd>{events.length}</dd></div></dl>
    <p className="hint">Exact backend event dates and times in UTC, including milliseconds. Processing durations exclude presentation animation and browser polling. Sources run in parallel.</p>
    <details open><summary>Processing and audit events</summary><div className="table-scroll"><table><thead><tr><th>Date / time (UTC)</th><th>Elapsed</th><th>Actor</th><th>Event</th><th>Evidence</th></tr></thead><tbody>{stages.map((event) => <tr key={event.key}><td className="mono"><EventTime at={event.at} /></td><td className="mono">{event.elapsed_ms == null ? '—' : `${event.elapsed_ms} ms`}</td><td>{event.actor}</td><td>{event.stage}</td><td className="mono trace-detail">{event.stage === 'Case created' ? `Scenario: ${event.detail.scenario}; ${event.detail.consents.sources.length} consent outcomes; model ${event.detail.model_sha256.slice(0, 12)}…` : JSON.stringify(event.detail)}</td></tr>)}</tbody></table></div></details>
    <details><summary>Individual bank API responses</summary><div className="table-scroll"><table><thead><tr><th>Date / time (UTC)</th><th>Source</th><th>Request</th><th>Status</th><th>Duration</th></tr></thead><tbody>{calls.map(({ at, detail, key }) => <tr key={key}><td className="mono"><EventTime at={at} /></td><td>{detail.source}</td><td className="mono">{detail.method} {detail.route}</td><td>{detail.http_status}</td><td>{detail.duration_ms} ms</td></tr>)}</tbody></table></div></details>
    <p className="hint">{audit?.limitation} Source tokens, full personal identifiers and bank credentials are not included in the audit.</p>
    <p className="hint mono">Model SHA-256: {job.model_sha256}<br />Evidence SHA-256: {job.result.evidence.snapshot_sha256}</p>
  </section>
}

export default function CaseWorkspace({ initialJob: job }) {
  const [audit, setAudit] = useState(null)
  const [error, setError] = useState(null)
  const [review, setReview] = useState(job.review ?? null)
  const loadAudit = () => getAudit(job.job_id).then(setAudit).catch((e) => setError(e.message))
  useEffect(() => { loadAudit() }, [job.job_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const decided = (decision) => { setReview(decision); loadAudit() }
  return <div className="case-workspace enter">
    {job.scenario !== 'none' && <section className="card"><p className="notice warn"><WarningLabel />: Explicit fault simulation: {job.scenario === 'rs_unavailable' ? 'Revenue Service returns HTTP 503' : 'Connected banks return HTTP 503'}.</p></section>}
    {error && <p className="error" role="alert"><WarningLabel />: {error}</p>}
    <section className="card"><h3>Source outcomes</h3><Progress job={job} /><details><summary>Consent scope and source authorization</summary><p className="hint">{job.consent_summary.mode}</p><p>{job.consent_summary.scope.join(' · ')}</p><ul>{job.consent_summary.sources.map((s) => <li key={s.source}>{s.source}: {s.status} · consent {s.consent}</li>)}</ul></details></section>
    <Result result={job.result} caseId={job.job_id} review={review} onDecided={decided} /><Evidence evidence={job.result.evidence} /><ProcessingAudit job={job} audit={audit} />
  </div>
}
