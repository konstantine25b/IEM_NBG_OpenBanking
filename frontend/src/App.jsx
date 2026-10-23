import { useEffect, useRef, useState } from 'react'
import { connectAll, getCase, getCases, getDemoClients, getEstimate, getSession, getSources, signIn, signOut, startEstimate } from './api.js'
import ClientPicker from './components/ClientPicker.jsx'
import ConnectingPanel from './components/ConnectingPanel.jsx'
import ConsentTable from './components/ConsentTable.jsx'
import Progress from './components/Progress.jsx'
import CaseWorkspace from './components/CaseWorkspace.jsx'
import Evaluation from './components/Evaluation.jsx'
import './app.css'

function Login({ onSignedIn }) {
  const [username, setUsername] = useState('officer')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const login = async (event) => {
    event.preventDefault(); setError(null); setBusy(true)
    try { onSignedIn(await signIn(username, password)) } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <main className="login"><section className="card"><h2>Sign in to the local demo</h2><p>Officer access can create and review income assessments. Auditor access can inspect saved cases and exports.</p>
    <form className="review-form" onSubmit={login}><label>Account<select value={username} onChange={(e) => setUsername(e.target.value)}><option value="officer">Officer</option><option value="auditor">Auditor (read only)</option></select></label>
      <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}<button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form><p className="hint">Local demo defaults: officer / <code>demo-review-only</code>; auditor / <code>demo-audit-only</code>. Server environment settings can replace these passwords. This is a prototype access boundary; bank SSO is not configured.</p>
  </section></main>
}

export default function App() {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)
  const [personalId, setPersonalId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [consents, setConsents] = useState(null)
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [demoClients, setDemoClients] = useState([])
  const [sources, setSources] = useState([])
  const [cases, setCases] = useState([])
  const [scenario, setScenario] = useState('none')
  const [showEvaluation, setShowEvaluation] = useState(false)
  const timer = useRef(null)
  const generation = useRef(0)
  const autoRan = useRef(false)

  const refreshCases = () => getCases().then(setCases).catch((e) => setError(e.message))
  const reset = () => { clearTimeout(timer.current); generation.current += 1; setConsents(null); setJob(null); setError(null) }
  const poll = (caseId, version) => getEstimate(caseId).then((state) => {
    if (version !== generation.current) return
    setJob(state)
    if (!['done', 'failed'].includes(state.status)) timer.current = setTimeout(() => poll(caseId, version), 250)
    else refreshCases()
  }).catch((e) => { if (version === generation.current) setError(e.message) })
  const estimate = async (id, failure = scenario) => {
    setError(null); setJob({ status: 'queued', stage: 'queued', progress: [], trace: [] })
    const version = generation.current
    try { const next = await startEstimate(id, null, failure); poll(next.job_id, version) } catch (e) { setJob(null); setError(e.message) }
  }
  const connect = async (id, run = false) => {
    reset(); setConnecting(true)
    try { const result = await connectAll(id); setConsents(result); if (run) await estimate(result.consent_set_id, 'none') }
    catch (e) { setError(e.status === 404 ? 'No mock bank knows this personal ID.' : e.message) }
    finally { setConnecting(false) }
  }
  useEffect(() => { getSession().then(setUser).catch(() => {}).finally(() => setReady(true)); return () => clearTimeout(timer.current) }, [])
  useEffect(() => {
    if (!user) return
    getDemoClients().then(setDemoClients).catch((e) => setError(e.message))
    getSources().then(setSources).catch((e) => setError(e.message))
    refreshCases()
    const auto = new URLSearchParams(window.location.search).get('client')
    if (auto && !autoRan.current && user.role === 'officer') { autoRan.current = true; setPersonalId(auto); connect(auto, true) }
    // The automatic demo runs once per signed-in browser session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user])
  const openCase = async (id) => { reset(); try { setJob(await getCase(id)) } catch (e) { setError(e.message) } }
  const logout = async () => { try { await signOut(); reset(); setCases([]); setUser(null) } catch (e) { setError(e.message) } }
  const running = Boolean(job) && !['done', 'failed'].includes(job.status)
  return <div className="shell">
    <header className="bar"><div className="brand"><span className="logo" aria-hidden="true">₾</span><div><h1>Income Check</h1><p>Income assessment from open banking, RS and Creditinfo</p></div></div>
      <div className="toolbar"><span className="demo-flag">Demo · synthetic data · automatic mock consent</span>{user && <><span>{user.username} · {user.role}</span><button className="secondary" onClick={logout}>Sign out</button></>}</div>
    </header>
    {!ready ? <main className="login"><p>Checking session…</p></main> : !user ? <Login onSignedIn={setUser} /> : <div className="layout">
      <div className="sidebar-stack">{user.role === 'officer' && <ClientPicker personalId={personalId} onChange={setPersonalId} onSubmit={(event) => { event.preventDefault(); connect(personalId.trim()) }} busy={connecting || running} connecting={connecting} clients={demoClients} />}
        <section className="card history"><h2>Saved cases</h2><p className="hint">Stored locally; available after a server restart.</p><button className="secondary" onClick={refreshCases}>Refresh cases</button>
          <ul className="case-list">{cases.map((c) => <li key={c.case_id}><button className="secondary" disabled={running || connecting} onClick={() => openCase(c.case_id)}><strong>{c.subject} · {c.case_id}</strong><small>{c.scenario} · {c.review?.decision || c.status} · {new Date(c.updated_at).toLocaleString()}</small></button></li>)}</ul>
        </section><button className="secondary" onClick={() => setShowEvaluation(!showEvaluation)}>{showEvaluation ? 'Hide' : 'Show'} model-risk evaluation</button>
      </div>
      <main className="main">{error && <p className="error" role="alert">{error}</p>}
        {!consents && !job && !connecting && <section className="card empty"><h2>{user.role === 'officer' ? 'Start with a client' : 'Select a saved case'}</h2><p>Assess recurring income, inspect the evidence, and record an officer review. Lending decisions remain outside this prototype.</p></section>}
        {connecting && <section className="card"><h2>Connecting financial sources</h2><ConnectingPanel sources={sources} /></section>}
        {consents && <section className="card"><h2>Connected sources</h2><ConsentTable consents={consents} />
          <div className="toolbar"><label>Recording scenario<select value={scenario} disabled={running} onChange={(e) => setScenario(e.target.value)}><option value="none">Normal collection</option><option value="rs_unavailable">Simulate RS HTTP 503 (partial-data fallback)</option><option value="banks_unavailable">Simulate bank HTTP 503 (withhold estimate)</option></select></label>
            <button disabled={running || consents.connected_banks === 0} onClick={() => estimate(consents.consent_set_id)}>{running ? 'Processing…' : job ? 'Run another assessment' : 'Estimate income'}</button></div>
          {scenario !== 'none' && <p className="hint">Explicit demo fault injection at the mock source. The original data is unchanged.</p>}
        </section>}
        {running && <section className="card"><h2>Collecting and processing evidence</h2><Progress job={job} /></section>}
        {job?.status === 'failed' && <p className="error" role="alert">Assessment failed: {job.error}</p>}
        {job?.status === 'done' && <CaseWorkspace key={job.job_id} initialJob={job} user={user} onUpdated={refreshCases} />}
        {showEvaluation && <Evaluation user={user} />}
      </main>
    </div>}
  </div>
}
