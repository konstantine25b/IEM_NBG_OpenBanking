import ClientProfile from './ClientProfile.jsx'

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

function SourceChip({ label, entry }) {
  const ok = entry.status === 'connected'
  return <span className={`pill ${ok ? 'ok' : 'bad'}`}>{label} {ok ? '✓' : '✕'}</span>
}

export default function ConsentTable({ consents }) {
  const { banks, rs, creditinfo, kyc, connected_banks: connected } = consents
  const held = banks.filter((b) => b.status === 'connected')
  const none = banks.filter((b) => b.status === 'no_relationship')
  const failed = banks.filter((b) => b.status !== 'connected' && b.status !== 'no_relationship')
  return (
    <div className="consents">
      <ClientProfile kyc={kyc} />
      <p className="summary">
        <strong>{connected}</strong> of {banks.length} banks hold accounts for this client
      </p>
      <div className="chips">
        <SourceChip label="Revenue Service" entry={rs} />
        <SourceChip label="Creditinfo" entry={creditinfo} />
      </div>

      {held.length > 0 && (
        <table>
          <thead>
            <tr><th>Bank</th><th>Result</th><th className="num-col">Accounts</th><th>Consent ID</th></tr>
          </thead>
          <tbody>
            {[...held, ...failed].map((b, i) => (
              <tr key={b.bank} className="row-in" style={{ animationDelay: `${i * 110}ms` }}>
                <td>{b.name}</td>
                <td>
                  <span className={`pill ${b.status === 'connected' ? 'ok' : 'bad'}`}>
                    {b.status === 'connected' ? '✓ Consent valid' : 'Error'}
                  </span>
                </td>
                <td className="num-col">{b.status === 'connected' ? plural(b.accounts, 'account') : '—'}</td>
                <td className="mono">{b.status === 'connected' ? `${b.consent_id.slice(0, 12)}…` : b.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {none.length > 0 && (
        <details className="no-account">
          <summary>No account at {plural(none.length, 'bank')} (left out)</summary>
          <p>{none.map((b) => b.name).join(' · ')}</p>
        </details>
      )}
    </div>
  )
}
