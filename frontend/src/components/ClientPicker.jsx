export default function ClientPicker({ personalId, onChange, onSubmit, busy, connecting, clients }) {
  return (
    <aside className="side">
      <form className="lookup" onSubmit={onSubmit}>
        <label htmlFor="pid">Client personal ID</label>
        <input
          id="pid"
          value={personalId}
          onChange={(e) => onChange(e.target.value)}
          placeholder="11 digits, e.g. 88148312536"
          inputMode="numeric"
          autoComplete="off"
          disabled={busy}
        />
        <button type="submit" disabled={!personalId.trim() || busy}>
          {connecting ? 'Connecting…' : 'Connect all banks'}
        </button>
      </form>

      {clients.length > 0 && (
        <>
          <h2 className="side-title">Demo clients</h2>
          <p className="hint">Ten kinds of person, from the clearest estimate to the hardest. None of them was used to train the model.</p>
          <ul className="clients">
            {clients.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`client ${personalId === c.personal_id ? 'active' : ''}`}
                  onClick={() => onChange(c.personal_id)}
                  disabled={busy}
                  title={c.story}
                >
                  <strong>{c.title}</strong>
                  <span className="meta">{c.facts}</span>
                  <span className="tags">
                    {c.banks.map((b) => <em key={b} className="tag">{b}</em>)}
                    {c.has_deposit && <em className="tag deposit">deposit</em>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  )
}
