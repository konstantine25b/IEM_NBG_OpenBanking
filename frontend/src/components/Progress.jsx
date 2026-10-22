const ICON = { pending: '○', running: null, done: '✓', failed: '✕' }
const STAGES = { queued: 'Queued', fetching: 'Fetching data', cleaning: 'Cleaning the data', scoring: 'Scoring', done: 'Done', failed: 'Failed' }

export default function Progress({ job }) {
  return (
    <div>
      <p className="hint">{STAGES[job.stage] ?? job.stage}…</p>
      <ul className="progress">
        {job.progress.map((p) => (
          <li key={p.source} className={p.state}>
            <span className="icon" aria-hidden="true">{ICON[p.state] ?? <span className="spinner" />}</span>
            <span className="label">{p.label}</span>
            <span className="detail">{p.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
