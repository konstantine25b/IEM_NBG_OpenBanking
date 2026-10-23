import { useState } from 'react'

const narrow = () => window.matchMedia('(max-width: 980px)').matches

export default function ClientPicker({ personalId, onChange, onSubmit, busy, connecting, clients }) {
  // on a phone the ten cards would push the result far down, so the list starts folded and folds after a pick
  const [open, setOpen] = useState(() => !narrow())
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
        <details className="demo-list" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
          <summary>Demo clients ({clients.length})</summary>
          <p className="hint">{clients.length} demo clients in two sets of 10, each from the clearest estimate to the hardest. None of them was used to train the model.</p>
          <ul className="clients">
            {clients.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`client ${personalId === c.personal_id ? 'active' : ''}`}
                  onClick={() => {
                    onChange(c.personal_id)
                    if (narrow()) setOpen(false)
                  }}
                  disabled={busy}
                  title={c.story}
                >
                  <strong>{c.title}</strong>
                  <span className="meta mono">{c.id}</span>
                  <span className="meta">{c.facts}</span>
                  <span className="tags">
                    {c.banks.map((b) => <em key={b} className="tag">{b}</em>)}
                    {c.has_deposit && <em className="tag deposit">deposit</em>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </aside>
  )
}
