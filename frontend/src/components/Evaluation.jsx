import { useEffect, useState } from 'react'
import { getEvaluation, runEvaluation } from '../api.js'

export default function Evaluation({ user }) {
  const [state, setState] = useState({ status: 'loading' })
  const [error, setError] = useState(null)
  useEffect(() => {
    let active = true
    const poll = () => getEvaluation().then((next) => { if (active) setState(next) }).catch((e) => { if (active) setError(e.message) })
    poll(); const timer = setInterval(poll, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [])
  const run = async () => { setError(null); try { setState(await runEvaluation()) } catch (e) { setError(e.message) } }
  const report = state.report
  return <section className="card evaluation"><h2>Held-out model-risk and subgroup evidence</h2><p className="hint">Synthetic evaluation only. No claim of production accuracy, fairness certification, regulatory compliance, or increased loan approvals.</p>
    {error && <p className="error" role="alert">{error}</p>}
    {report ? <><p>{report.population.toLocaleString()} held-out customers · generated {new Date(report.generated_at).toLocaleString()}.</p>
      <dl className="facts"><div><dt>Mean absolute error</dt><dd>{report.overall.mae_gel} GEL</dd></div><div><dt>P10–P90 coverage</dt><dd>{report.overall.interval_coverage_pct}%</dd></div><div><dt>Salary baseline abstentions</dt><dd>{report.salary_baseline.abstained.toLocaleString()}</dd></div></dl>
      <p>On the same {report.salary_baseline.covered.toLocaleString()} salary-covered customers: salary-only MAE {report.salary_baseline.salary_mae_on_covered_gel} GEL; model MAE {report.salary_baseline.model_mae_on_same_covered_gel} GEL.</p><p className="notice warn">{report.feature_risks}</p>
      <details><summary>Inspect subgroup errors, coverage and sample sizes</summary><div className="table-scroll"><table><thead><tr><th>Dimension / group</th><th>Customers</th><th>MAE (GEL)</th><th>P10–P90 coverage</th></tr></thead><tbody>{report.groups.map((g) => <tr key={`${g.dimension}-${g.group}`}><td>{g.dimension}: {g.group}{g.small_sample && ' · small sample'}</td><td>{g.n}</td><td>{g.mae_gel}</td><td>{g.interval_coverage_pct}%</td></tr>)}</tbody></table></div><p>Observed differences among groups with at least 100 customers:</p><ul>{report.group_gaps.map((g) => <li key={g.dimension}>{g.dimension}: MAE range {g.mae_range_gel} GEL; interval coverage range {g.coverage_range_pp} percentage points.</li>)}</ul></details>
      <p className="hint">{report.basis}. {report.limitations}</p></> : <p>Evaluation status: {state.status}. Results appear only after the existing model has actually been evaluated.</p>}
    {user.role === 'officer' && <button className="secondary" onClick={run} disabled={state.status === 'running'}>{state.status === 'running' ? 'Evaluating existing model…' : 'Run held-out evaluation'}</button>}
  </section>
}
