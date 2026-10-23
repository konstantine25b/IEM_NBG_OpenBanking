const ICON = { pending: '○', running: '↻', done: '✓', failed: '✕' }

export default function Progress({ job }) {
  return <div>
    <p className="hint">Actual source status. Requests run in parallel; completed events remain available in the case trace.</p>
    <ul className="progress">{(job.progress || []).map((p) => <li key={p.source} className={p.state}>
      <span className="icon" aria-hidden="true">{ICON[p.state]}</span><span className="label">{p.label}</span><span className="detail">{p.detail || p.state}</span>
    </li>)}</ul>
    <ol className="trace-preview">{(job.trace || []).slice(-5).map((event, i) => <li key={i}><span className="mono">{event.elapsed_ms} ms</span> {event.stage}</li>)}</ol>
  </div>
}
