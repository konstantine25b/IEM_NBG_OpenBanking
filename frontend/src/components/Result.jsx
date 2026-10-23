import { useCountUp, useMounted } from '../hooks.js'

const gel = (n) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(n)

const LEVEL_CLASS = { 'very high': 'ok', high: 'ok', medium: 'warn', low: 'bad', 'very low': 'bad' }

// The bar shows only the P10-P90 neighbourhood: a band that is 1% wide would vanish on a 0..max scale.
function RangeBar({ p10, p50, p90 }) {
  const grown = useMounted(250)
  const pad = Math.max((p90 - p10) * 0.6, p50 * 0.05, 1)
  const lo = Math.max(0, p10 - pad)
  const hi = p90 + pad
  const pct = (v) => ((v - lo) / (hi - lo)) * 100
  // starts as a sliver at P50 and widens to P10..P90
  const band = grown
    ? { left: `${pct(p10)}%`, width: `${pct(p90) - pct(p10)}%` }
    : { left: `${pct(p50)}%`, width: '0%' }
  return (
    <div className="range" role="img" aria-label={`P10 ${gel(p10)}, P50 ${gel(p50)}, P90 ${gel(p90)} GEL per month`}>
      <div className="track">
        <div className="band" style={band} />
        <div className={`median ${grown ? 'in' : ''}`} style={{ left: `${pct(p50)}%` }} />
      </div>
      <div className={`legend ${grown ? 'in' : ''}`}>
        <span className="lo"><b>{gel(p10)}</b><small>P10 · conservative</small></span>
        <span className="mid"><b>{gel(p50)}</b><small>P50 · most likely</small></span>
        <span className="hi"><b>{gel(p90)}</b><small>P90 · optimistic</small></span>
      </div>
    </div>
  )
}

function ConfidenceDetail({ confidence, income }) {
  const m = confidence.measured
  const shown = useCountUp(confidence.score ?? 0, 1600)
  const grown = useMounted(400)
  return (
    <section className="card rise">
      <h3>How sure is this?</h3>
      {confidence.score != null && (
        <div className="score">
          <div className="score-num"><strong>{Math.round(shown)}/100</strong> confidence score</div>
          <div className="score-track" role="img" aria-label={`Confidence score ${confidence.score} out of 100`}>
            <div className={`score-fill ${LEVEL_CLASS[confidence.level] ?? 'warn'}`} style={{ width: grown ? `${confidence.score}%` : '0%' }} />
          </div>
          <p className="hint">
            Score out of 100 = 100 × (1 − relative band width). The P10–P90 band spans <strong>{gel(income.p90 - income.p10)} GEL</strong>{' '}
            ({(confidence.band_share * 100).toFixed(1)}% of the P50), so the estimate is{' '}
            <strong>P50 ±{confidence.plus_minus_pct}%</strong>. It shows how precise the range is; it is not the chance of
            being right.
          </p>
          <p className="hint scale">
            Labels by band width:{' '}
            {confidence.scale.map((l, i) => (
              <span key={l.level} className={l.level === confidence.level ? 'on' : ''}>
                {i > 0 && ' · '}
                {l.level} {l.max_band_pct == null ? `>${confidence.scale[i - 1].max_band_pct}%` : `≤${l.max_band_pct}%`}
              </span>
            ))}
          </p>
        </div>
      )}
      {m ? (
        <>
          <ul className="measured">
            <li><strong>{m.inside_p10_p90_pct}%</strong> of clients with this label had their real income inside P10–P90 (the band is built for 80%)</li>
            <li><strong>{m.median_error_pct}%</strong> median error of the P50</li>
            <li><strong>{m.within_10pct_pct}%</strong> of P50 estimates were within ±10% of the real income</li>
          </ul>
          <p className="hint">Measured on {m.basis}.</p>
        </>
      ) : (
        <p className="hint">No matched calibration is available for this source combination or data-sufficiency outcome.</p>
      )}
    </section>
  )
}

function Drivers({ explanation }) {
  const grown = useMounted(600)
  if (!explanation) return null
  const { drivers, starting_point_gel: start } = explanation
  const max = Math.max(...drivers.map((d) => Math.abs(d.effect_gel)), 1)
  // when every driver pushes the same way, the bars use the whole width instead of half of it
  const hasNeg = drivers.some((d) => d.effect_gel < 0)
  const hasPos = drivers.some((d) => d.effect_gel > 0)
  const width = (d) => (grown ? `${(Math.abs(d.effect_gel) / max) * 100}%` : '0%')
  return (
    <section className="card rise late">
      <h3>Why this estimate</h3>
      <p className="hint">
        Starts from the average client ({gel(start)} GEL); each factor moves it up or down (SHAP values of the P50 model).
      </p>
      <ul className="drivers">
        {drivers.map((d, i) => (
          <li key={d.driver}>
            <span className="name">{d.driver}</span>
            <span className="bars" aria-hidden="true">
              <span className="half left" style={{ flex: hasNeg ? 1 : 0 }}>{d.effect_gel < 0 && <i className="neg" style={{ width: width(d), transitionDelay: `${i * 140}ms` }} />}</span>
              <span className="half right" style={{ flex: hasPos ? 1 : 0 }}>{d.effect_gel > 0 && <i className="pos" style={{ width: width(d), transitionDelay: `${i * 140}ms` }} />}</span>
            </span>
            <span className={`val ${d.effect_gel < 0 ? 'neg' : 'pos'}`}>{d.effect_gel > 0 ? '+' : '−'}{gel(Math.abs(d.effect_gel))}</span>
          </li>
        ))}
      </ul>
      <p className="hint">= {gel(explanation.reported_p50_gel)} GEL (P50)</p>
    </section>
  )
}

export default function Result({ result }) {
  const { income, confidence, warnings, data, cleaning, explanation } = result
  const p50 = useCountUp(income?.p50 ?? 0, 1500)
  const levelClass = LEVEL_CLASS[confidence.level] ?? 'warn'
  return (
    <div className="result">
      {income ? <section className="card hero enter">
        <div className="headline">
          <div>
            <div className="eyebrow">Estimated sustainable income</div>
            <div className="big">{gel(p50)} <small>GEL / month</small></div>
          </div>
          <span className={`badge ${levelClass}`}>
            Confidence: {confidence.level}{confidence.score != null && ` · ${confidence.score}/100`}
          </span>
        </div>
        <RangeBar p10={income.p10} p50={income.p50} p90={income.p90} />
      </section> : <section className="card notice bad"><h2>Insufficient evidence — estimate withheld</h2><p>Request more bank evidence. An officer cannot accept an estimate that was not produced.</p></section>}

      {(confidence.downgrade_reasons.length > 0 || warnings.length > 0) && (
        <section className="notices">
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
        </section>
      )}

      <div className="two">
        {income && <ConfidenceDetail confidence={confidence} income={income} />}
        <Drivers explanation={explanation} />
      </div>

      <section className="card rise late">
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
              <tr><td><strong>Transactions retained for analysis</strong></td><td className="num-col"><strong>{gel(cleaning.used_for_income)}</strong></td></tr>
            </tbody>
          </table>
        </details>
        <p className="hint">All data is synthetic; the estimate shows how the method works, not real-world accuracy.</p>
      </section>
    </div>
  )
}
