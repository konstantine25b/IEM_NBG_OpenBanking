import { useCallback, useEffect, useRef, useState } from 'react'
import { connectAll, getDemoClients, getEstimate, getSources, startEstimate } from './api.js'
import ClientPicker from './components/ClientPicker.jsx'
import ConnectingPanel from './components/ConnectingPanel.jsx'
import ConsentTable from './components/ConsentTable.jsx'
import { AnimatedProgress } from './components/Progress.jsx'
import CaseWorkspace from './components/CaseWorkspace.jsx'
import Evaluation from './components/Evaluation.jsx'
import WarningLabel from './components/WarningLabel.jsx'
import './app.css'

const STEPS = ['Connect banks', 'Collect data', 'Result']

function Stepper({ states }) {
  return <ol className="stepper" aria-label="Progress">{STEPS.map((label, i) => <li key={label} className={states[i]} aria-current={states[i] === 'active' ? 'step' : undefined}>
    <span className="dot">{states[i] === 'done' ? '✓' : i + 1}</span>{label}
  </li>)}</ol>
}

export default function App() {
  const [personalId, setPersonalId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [reply, setReply] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const [consents, setConsents] = useState(null)
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [demoClients, setDemoClients] = useState([])
  const [sources, setSources] = useState([])
  const [showEvaluation, setShowEvaluation] = useState(false)
  const timer = useRef(null)
  const generation = useRef(0)
  const autoRan = useRef(false)
  const played = useRef(null)
  const mainRef = useRef(null)

  const reset = () => { clearTimeout(timer.current); played.current?.(); played.current = null; generation.current += 1; setConnecting(false); setReply(null); setRevealed(false); setConsents(null); setJob(null); setError(null) }
  const onPlayed = useCallback(() => { played.current?.(); played.current = null }, [])
  const onFinished = useCallback(() => setRevealed(true), [])
  const poll = (caseId, version) => getEstimate(caseId).then((state) => {
    if (version !== generation.current) return
    setJob(state)
    if (!['done', 'failed'].includes(state.status)) timer.current = setTimeout(() => poll(caseId, version), 250)
  }).catch((e) => { if (version === generation.current) setError(e.message) })
  const estimate = async (id) => {
    setError(null); setRevealed(false); setJob({ status: 'queued', stage: 'queued', progress: [], trace: [] })
    const version = generation.current
    try { const next = await startEstimate(id); poll(next.job_id, version) } catch (e) { setJob(null); setError(e.message) }
  }
  const connect = async (id, run = false) => {
    reset(); setConnecting(true)
    const version = generation.current
    try {
      const known = sources.length ? sources : await getSources().catch(() => [])
      if (!sources.length && known.length) setSources(known)
      const result = await connectAll(id)
      if (version !== generation.current) return
      const animation = known.length ? new Promise((resolve) => { played.current = resolve }) : Promise.resolve()
      setReply(result)
      await animation
      if (version !== generation.current) return
      setConsents(result)
      if (run) await estimate(result.consent_set_id)
    }
    catch (e) { if (version === generation.current) setError(e.status === 404 ? 'No mock bank knows this personal ID.' : e.message) }
    finally { if (version === generation.current) { setConnecting(false); setReply(null) } }
  }
  useEffect(() => {
    getDemoClients().then(setDemoClients).catch((e) => setError(e.message))
    getSources().then(setSources).catch((e) => setError(e.message))
    const auto = new URLSearchParams(window.location.search).get('client')
    if (auto && !autoRan.current) { autoRan.current = true; setPersonalId(auto); connect(auto, true) }
    return () => { clearTimeout(timer.current); played.current?.() }
    // The automatic demo runs once when this page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const running = Boolean(job) && job.status !== 'failed' && (job.status !== 'done' || !revealed)
  const showResult = job?.status === 'done' && revealed
  const stepStates = showResult ? ['done', 'done', 'done'] : job ? ['done', 'active', 'todo'] : consents ? ['done', 'next', 'todo'] : ['active', 'todo', 'todo']
  useEffect(() => {
    if ((connecting || showResult) && window.matchMedia('(max-width: 980px)').matches) mainRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [connecting, showResult])
  return <div className="shell">
    <header className="bar"><div className="brand"><span className="logo" aria-hidden="true">₾</span><div><h1>Income Check</h1><p>Income assessment from open banking, RS and Creditinfo</p></div></div>
      <div className="toolbar"><span className="demo-flag">Demo · synthetic data · automatic mock consent</span></div>
    </header>
    <div className="layout">
      <div className="sidebar-stack"><ClientPicker personalId={personalId} onChange={setPersonalId} onSubmit={(event) => { event.preventDefault(); connect(personalId.trim()) }} busy={connecting || running} connecting={connecting} clients={demoClients} />
        <button className="secondary" onClick={() => setShowEvaluation(!showEvaluation)}>{showEvaluation ? 'Hide' : 'Show'} model-risk evaluation</button>
      </div>
      <main className="main" ref={mainRef}><Stepper states={stepStates} />{error && <p className="error" role="alert"><WarningLabel />: {error}</p>}
        {!consents && !job && !connecting && <section className="card empty"><h2>Start with a client</h2><p>Assess recurring income and inspect the evidence. Lending decisions remain outside this prototype.</p></section>}
        {connecting && sources.length > 0 && <section className="card enter"><h2>Connecting financial sources</h2><ConnectingPanel sources={sources} result={reply} onDone={onPlayed} /></section>}
        {consents && <section className="card enter"><h2>Connected sources</h2><ConsentTable consents={consents} />
          <div className="toolbar"><button disabled={running || consents.connected_banks === 0} onClick={() => estimate(consents.consent_set_id)}>{running ? 'Processing…' : job ? 'Run another assessment' : 'Estimate income'}</button></div>
        </section>}
        {running && <section className="card enter"><h2>Collecting and processing evidence</h2><AnimatedProgress job={job} onFinished={onFinished} /></section>}
        {job?.status === 'failed' && <p className="error" role="alert"><WarningLabel />: Assessment failed: {job.error}</p>}
        {job?.status === 'done' && revealed && <CaseWorkspace key={job.job_id} initialJob={job} />}
        {showEvaluation && <Evaluation />}
      </main>
    </div>
  </div>
}
