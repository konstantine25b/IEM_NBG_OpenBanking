import { useState } from 'react'
import { useCountUp, useMounted } from '../hooks.js'
import WarningLabel from './WarningLabel.jsx'

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
        <p className="hint"><WarningLabel />: No matched calibration is available for this source combination or data-sufficiency outcome.</p>
      )}
    </section>
  )
}

const signed = (n) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${gel(Math.abs(n))}`
const pctTitle = (d) =>
  `${d.share_pct}% of the total impact of all factors` + (d.pct_of_p50 != null ? `; ${signed(d.pct_of_p50)}% of the estimate itself` : '')

function Bars({ value, max, grown, delay, hasNeg, hasPos }) {
  const width = grown ? `${(Math.abs(value) / max) * 100}%` : '0%'
  const style = { width, transitionDelay: `${delay}ms` }
  return (
    <span className="bars" aria-hidden="true">
      <span className="half left" style={{ flex: hasNeg ? 1 : 0 }}>{value < 0 && <i className="neg" style={style} />}</span>
      <span className="half right" style={{ flex: hasPos ? 1 : 0 }}>{value > 0 && <i className="pos" style={style} />}</span>
    </span>
  )
}

function Drivers({ explanation }) {
  const grown = useMounted(600)
  // ?factors=all in the page address opens the full list (handy for a link or a screenshot)
  const [showAll, setShowAll] = useState(() => new URLSearchParams(window.location.search).has('factors'))
  if (!explanation) return null
  const rows = showAll ? explanation.all_drivers : explanation.drivers
  const max = Math.max(...rows.map((d) => Math.abs(d.effect_gel)), 1)
  // when every factor pushes the same way, the bars use the whole width instead of half of it
  const hasNeg = rows.some((d) => d.effect_gel < 0)
  const hasPos = rows.some((d) => d.effect_gel > 0)
  const bars = (value, i) => <Bars value={value} max={max} grown={grown} delay={i * 140} hasNeg={hasNeg} hasPos={hasPos} />
  return (
    <section className="card rise late">
      <div className="drivers-head">
        <h3>Why this estimate</h3>
        <button type="button" className="link" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
          {showAll ? 'Show fewer' : 'Show all factors'}
        </button>
      </div>
      <p className="hint">
        Starts from the average client ({gel(explanation.starting_point_gel)} GEL); each factor moves it up or down (SHAP values of the P50 model).
        <strong> Share</strong> is the factor&apos;s size as a % of all factors added together (100% in total).
      </p>
      <div className="drivers-cols" aria-hidden="true">
        <span>Factor</span><span /><span className="r">Effect, GEL</span><span className="r">Share</span>
      </div>
      <ul className="drivers">
        {rows.map((d, i) => (
          <li key={d.driver}>
            {showAll && d.features?.length ? (
              <details>
                <summary className="drow">
                  <span className="name"><span className="chev" aria-hidden="true" />{d.driver}</span>
                  {bars(d.effect_gel, i)}
                  <span className={`val ${d.effect_gel < 0 ? 'neg' : 'pos'}`}>{signed(d.effect_gel)}</span>
                  <span className="share" title={pctTitle(d)}>{d.share_pct.toFixed(1)}%</span>
                </summary>
                <ul className="features">
                  {d.features.map((f) => (
                    <li key={f.feature} className="drow sub">
                      <span className="name">{f.label}{f.value != null && <em> = {gel(f.value)}</em>}</span>
                      <span />
                      <span className={`val ${f.effect_gel < 0 ? 'neg' : 'pos'}`}>{signed(Math.round(f.effect_gel))}</span>
                      <span className="share">{f.share_pct.toFixed(1)}%</span>
                    </li>
                  ))}
                  {d.features_not_shown > 0 && <li className="more">+ {d.features_not_shown} features with a tiny effect</li>}
                </ul>
              </details>
            ) : (
              <div className="drow">
                <span className="name">{d.driver}</span>
                {bars(d.effect_gel, i)}
                <span className={`val ${d.effect_gel < 0 ? 'neg' : 'pos'}`}>{signed(d.effect_gel)}</span>
                <span className="share" title={pctTitle(d)}>{d.share_pct.toFixed(1)}%</span>
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="hint">= {gel(explanation.reported_p50_gel)} GEL (P50){showAll && ' · open a factor to see the model features inside it'}</p>
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
            {levelClass !== 'ok' && <><WarningLabel />: </>}Confidence: {confidence.level}{confidence.score != null && ` · ${confidence.score}/100`}
          </span>
        </div>
        <RangeBar p10={income.p10} p50={income.p50} p90={income.p90} />
      </section> : <section className="card notice bad"><h2><WarningLabel />: Insufficient evidence — estimate withheld</h2><p>Request more bank evidence. An officer cannot accept an estimate that was not produced.</p></section>}

      {(confidence.downgrade_reasons.length > 0 || warnings.length > 0) && (
        <section className="notices">
          {confidence.downgrade_reasons.length > 0 && (
            <div className="notice bad">
              <strong><WarningLabel />: Confidence lowered to “very low”</strong>
              <ul>{confidence.downgrade_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          )}
          {warnings.length > 0 && (
            <div className="notice warn">
              <ul>{warnings.map((w) => <li key={w}><WarningLabel />: {w}</li>)}</ul>
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
          <div><dt>Banks used</dt><dd>{data.banks_used_names.length ? data.banks_used_names.join(', ') : 'none'}</dd></div>
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
