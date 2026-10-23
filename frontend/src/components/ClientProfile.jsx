const fmtDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })

function Row({ label, value, source, children }) {
  return (
    <div className="prow">
      <dt>{label}</dt>
      <dd>
        <span className="pvalue">{value}</span>
        <span className="psource">{source}{children}</span>
      </dd>
    </div>
  )
}

// Who the client is, and where each fact came from.
export default function ClientProfile({ kyc }) {
  if (!kyc) return null
  const { provenance: from, name_check: checks } = kyc
  const place = kyc.city === kyc.region_name ? kyc.city : `${kyc.city}, ${kyc.region_name}`
  const confirmed = checks.filter((c) => c.matches)
  return (
    <div className="profile">
      <div className="avatar" aria-hidden="true">{kyc.name_en.split(' ').map((w) => w[0]).slice(0, 2).join('')}</div>
      <dl className="pgrid">
        <Row label="Name" value={<>{kyc.name_en} <span className="muted">· {kyc.name_ka}</span></>} source={from.name}>
          {checks.length > 0 && (
            <span className={`check ${confirmed.length === checks.length ? 'ok' : 'bad'}`}>
              {confirmed.length === checks.length ? '✓' : '✕'} matches the account owner at{' '}
              {confirmed.length === checks.length ? checks.map((c) => c.bank.toUpperCase()).join(', ') : 'only some banks'}
            </span>
          )}
        </Row>
        <Row label="Personal ID" value={<span className="mono-val">{kyc.personId}</span>} source={from.personal_id} />
        <Row label="Born" value={`${fmtDate(kyc.birth_date)} · age ${kyc.age}`} source={`${from.birth_date}; age: ${from.age.toLowerCase()}`} />
        <Row label="Lives in" value={place} source={from.place} />
      </dl>
      <p className="hint demo-note">{from.demo_note}</p>
    </div>
  )
}
