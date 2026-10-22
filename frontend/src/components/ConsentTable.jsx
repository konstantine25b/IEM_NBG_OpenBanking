const STATUS = {
  connected: { text: 'Consent valid', cls: 'ok' },
  no_relationship: { text: 'No account', cls: 'muted' },
  error: { text: 'Error', cls: 'bad' },
  unknown_client: { text: 'Unknown client', cls: 'bad' },
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

function SourceChip({ label, entry }) {
  const ok = entry.status === 'connected'
  return <span className={`pill ${ok ? 'ok' : 'bad'}`}>{label} {ok ? '✓' : '✕'}</span>
}

export default function ConsentTable({ consents }) {
  const { banks, rs, creditinfo, kyc, connected_banks: connected } = consents
  return (
    <div className="consents">
      <p className="summary">
        <strong>{connected}</strong> of {banks.length} banks hold accounts for this client
        {kyc && <span className="muted"> · age {Math.round(kyc.age)}, {kyc.city}, {kyc.region_name}</span>}
      </p>
      <div className="chips">
        <SourceChip label="Revenue Service" entry={rs} />
        <SourceChip label="Creditinfo" entry={creditinfo} />
      </div>
      <table>
        <thead>
          <tr><th>Bank</th><th>Result</th><th className="num-col">Accounts</th><th>Consent ID</th></tr>
        </thead>
        <tbody>
          {banks.map((b, i) => {
            const s = STATUS[b.status] ?? STATUS.error
            return (
              <tr key={b.bank} className={`row-in ${b.status === 'connected' ? '' : 'dim'}`} style={{ animationDelay: `${i * 110}ms` }}>
                <td>{b.name}</td>
                <td><span className={`pill ${s.cls}`}>{b.status === 'connected' ? '✓ ' : ''}{s.text}</span></td>
                <td className="num-col">{b.status === 'connected' ? plural(b.accounts, 'account') : '—'}</td>
                <td className="mono">{b.consent_id && b.status === 'connected' ? b.consent_id.slice(0, 12) + '…' : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
