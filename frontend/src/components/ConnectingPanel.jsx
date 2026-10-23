export default function ConnectingPanel({ sources }) {
  return <div>
    <p className="hint">Requesting simulated consent at all sources in parallel. Outcomes appear when the consent batch returns.</p>
    <ul className="progress sources">{sources.map((source) => <li key={source.key} className="running">
      <span className="icon"><span className="spinner" /></span><span className="label">{source.label}</span><span className="detail">Awaiting consent result</span>
    </li>)}</ul>
  </div>
}
