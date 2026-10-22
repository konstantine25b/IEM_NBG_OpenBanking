const gel = (n) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(n)

const LEVEL_CLASS = { 'very high': 'ok', high: 'ok', medium: 'warn', low: 'bad', 'very low': 'bad' }

// The bar shows only the P10-P90 neighbourhood: a band that is 1% wide would vanish on a 0..max scale.
function RangeBar({ p10, p50, p90 }) {
  const pad = Math.max((p90 - p10) * 0.6, p50 * 0.05, 1)
  const lo = Math.max(0, p10 - pad)
  const hi = p90 + pad
  const at = (v) => `${((v - lo) / (hi - lo)) * 100}%`
  return (
    <div className="range" role="img" aria-label={`P10 ${gel(p10)}, P50 ${gel(p50)}, P90 ${gel(p90)} GEL per month`}>
      <div className="track">
        <div className="band" style={{ left: at(p10), width: `calc(${at(p90)} - ${at(p10)})` }} />
        <div className="median" style={{ left: at(p50) }} />
      </div>
      <div className="ticks">
        <span style={{ left: at(p10) }}><b>{gel(p10)}</b><small>P10 · conservative</small></span>
        <span style={{ left: at(p50) }} className="mid"><b>{gel(p50)}</b><small>P50 · most likely</small></span>
        <span style={{ left: at(p90) }}><b>{gel(p90)}</b><small>P90 · optimistic</small></span>
      </div>
    </div>
  )
}

function ConfidenceDetail({ confidence }) {
  const m = confidence.measured
  return (
    <div className="panel">
      <h3>How sure is this?</h3>
      <p>
        The P10–P90 band is <strong>{(confidence.band_share * 100).toFixed(1)}%</strong> of the P50 wide.
      </p>
      {m ? (
        <>
          <ul className="measured">
            <li><strong>{m.inside_p10_p90_pct}%</strong> of clients with this label had their real income inside P10–P90 (the band is built for 80%)</li>
            <li><strong>{m.median_error_pct}%</strong> median error of the P50</li>
            <li><strong>{m.within_10pct_pct}%</strong> of P50 estimates were within ±10% of the real income</li>
          </ul>
          <p className="hint">Measured on {m.basis}. The label ranks how narrow the band is; it is not a promise of 80%.</p>
        </>
      ) : (
        <p className="hint">This label comes from the data-sufficiency rules, not from the model, so no accuracy has been measured for it.</p>
      )}
    </div>
  )
}

function Drivers({ explanation }) {
  if (!explanation) return null
  const { drivers, starting_point_gel: start } = explanation
  const max = Math.max(...drivers.map((d) => Math.abs(d.effect_gel)), 1)
  return (
    <div className="panel">
      <h3>Why this estimate</h3>
      <p className="hint">
        Starts from the average client ({gel(start)} GEL); each factor moves it up or down (SHAP values of the P50 model).
      </p>
      <ul className="drivers">
        {drivers.map((d) => (
          <li key={d.driver}>
            <span className="name">{d.driver}</span>
            <span className="bars" aria-hidden="true">
              <span className="half left">{d.effect_gel < 0 && <i className="neg" style={{ width: `${(Math.abs(d.effect_gel) / max) * 100}%` }} />}</span>
              <span className="half right">{d.effect_gel > 0 && <i className="pos" style={{ width: `${(d.effect_gel / max) * 100}%` }} />}</span>
            </span>
            <span className={`val ${d.effect_gel < 0 ? 'neg' : 'pos'}`}>{d.effect_gel > 0 ? '+' : '−'}{gel(Math.abs(d.effect_gel))}</span>
          </li>
        ))}
      </ul>
      <p className="hint">= {gel(explanation.reported_p50_gel)} GEL (P50)</p>
    </div>
  )
}

export default function Result({ result }) {
  const { income, confidence, warnings, data, cleaning, explanation } = result
  return (
    <div className="result">
      <div className="headline">
        <div>
          <div className="big">{gel(income.p50)} <small>GEL / month</small></div>
          <div className="muted">Estimated sustainable income</div>
        </div>
        <span className={`badge ${LEVEL_CLASS[confidence.level] ?? 'warn'}`}>Confidence: {confidence.level}</span>
      </div>

      <RangeBar p10={income.p10} p50={income.p50} p90={income.p90} />
      <ConfidenceDetail confidence={confidence} />
      <Drivers explanation={explanation} />

      {confidence.downgrade_reasons.length > 0 && (
        <div className="notice bad">
          <strong>Confidence lowered to “very low”</strong>
          <ul>{confidence.downgrade_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="notice warn">
          <strong>Warnings</strong>
          <ul>{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}

      <dl className="facts">
        <div><dt>Banks used</dt><dd>{data.banks_used.length ? data.banks_used.join(', ').toUpperCase() : 'none'}</dd></div>
        <div><dt>History</dt><dd>{data.months_covered} months</dd></div>
        <div><dt>Transactions used</dt><dd>{gel(data.transactions_used)}</dd></div>
      </dl>

      <details>
        <summary>How the data was cleaned</summary>
        <table className="small">
          <tbody>
            <tr><td>Transactions received</td><td className="num-col">{gel(cleaning.received)}</td></tr>
            <tr><td>Pending (not counted)</td><td className="num-col">{gel(cleaning.pending)}</td></tr>
            <tr><td>Card mirror entries</td><td className="num-col">{gel(cleaning.card_mirrors)}</td></tr>
            <tr><td>Transfers between own accounts</td><td className="num-col">{gel(cleaning.own_account_transfers)}</td></tr>
            <tr><td>Loan disbursements</td><td className="num-col">{gel(cleaning.loan_disbursements)}</td></tr>
            <tr><td>Refunds</td><td className="num-col">{gel(cleaning.refunds)}</td></tr>
            <tr><td><strong>Used for income</strong></td><td className="num-col"><strong>{gel(cleaning.used_for_income)}</strong></td></tr>
          </tbody>
        </table>
      </details>
      <p className="hint">All data is synthetic; the estimate shows how the method works, not real-world accuracy.</p>
    </div>
  )
}
