import { useCallback, useEffect, useRef, useState } from 'react'
import { connectAll, getDemoClients, getEstimate, startEstimate } from './api.js'
import ClientPicker from './components/ClientPicker.jsx'
import ConsentTable from './components/ConsentTable.jsx'
import Progress from './components/Progress.jsx'
import Result from './components/Result.jsx'
import { pace, wait } from './hooks.js'
import './app.css'

const POLL_MS = 700
const STEPS = ['Connect banks', 'Collect data', 'Result']

function Stepper({ current }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STEPS.map((label, i) => (
        <li key={label} className={i < current ? 'done' : i === current ? 'active' : ''} aria-current={i === current ? 'step' : undefined}>
          <span className="dot">{i < current ? '✓' : i + 1}</span>
          {label}
        </li>
      ))}
    </ol>
  )
}

export default function App() {
  const [personalId, setPersonalId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [consents, setConsents] = useState(null)
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [demoClients, setDemoClients] = useState([])
  const [revealed, setRevealed] = useState(false)
  const timer = useRef(null)
  const autoRan = useRef(false)

  const reset = () => {
    clearTimeout(timer.current)
    setConsents(null)
    setJob(null)
    setRevealed(false)
    setError(null)
  }

  const poll = (jobId) => {
    getEstimate(jobId)
      .then((state) => {
        setJob(state)
        if (state.status !== 'done' && state.status !== 'failed') {
          timer.current = setTimeout(() => poll(jobId), POLL_MS)
        }
      })
      .catch((e) => setError(e.message))
  }

  const connect = async (id) => {
    reset()
    setConnecting(true)
    try {
      // the call answers in a blink; hold the spinner a moment so the click visibly does something
      const [result] = await Promise.all([connectAll(id), wait(pace(900))])
      setConsents(result)
      return result
    } catch (e) {
      setError(e.status === 404 ? 'No bank knows this personal ID.' : e.message)
      return null
    } finally {
      setConnecting(false)
    }
  }

  const estimate = async (consentSetId) => {
    setError(null)
    setRevealed(false)
    setJob({ status: 'queued', stage: 'queued', progress: [] })
    try {
      const { job_id } = await startEstimate(consentSetId)
      poll(job_id)
    } catch (e) {
      setJob(null)
      setError(e.message)
    }
  }

  const onConnect = (event) => {
    event.preventDefault()
    connect(personalId.trim())
  }

  const onFinished = useCallback(() => setRevealed(true), [])

  useEffect(() => {
    getDemoClients().then(setDemoClients).catch(() => setDemoClients([]))
    // /?client=<personal id> runs the whole flow, so a demo can be opened from a link
    const auto = new URLSearchParams(window.location.search).get('client')
    if (auto && !autoRan.current) {
      autoRan.current = true
      setPersonalId(auto)
      connect(auto).then((r) => r?.connected_banks && estimate(r.consent_set_id))
    }
    return () => clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const running = Boolean(job) && job.status !== 'done' && job.status !== 'failed'
  const showResult = job?.status === 'done' && revealed
  const current = showResult ? 3 : job ? 1 : consents ? 1 : 0

  return (
    <div className="shell">
      <header className="bar">
        <div className="brand">
          <span className="logo" aria-hidden="true">₾</span>
          <div>
            <h1>Income Check</h1>
            <p>Income estimate from open banking, RS and Creditinfo</p>
          </div>
        </div>
        <span className="demo-flag">Demo · synthetic data</span>
      </header>

      <div className="layout">
        <ClientPicker
          personalId={personalId}
          onChange={setPersonalId}
          onSubmit={onConnect}
          busy={connecting || running}
          connecting={connecting}
          clients={demoClients}
        />

        <main className="main">
          <Stepper current={current} />
          {error && <p className="error" role="alert">{error}</p>}

          {!consents && !connecting && !error && (
            <section className="card empty">
              <h2>Start with a client</h2>
              <p>Pick a demo client on the left, or type a personal ID, then press <strong>Connect all banks</strong>.</p>
            </section>
          )}

          {consents && (
            <section className="card enter">
              <h2>Connected sources</h2>
              <ConsentTable consents={consents} />
              {!job && (
                <div className="actions">
                  <button type="button" onClick={() => estimate(consents.consent_set_id)} disabled={consents.connected_banks === 0}>
                    Estimate income
                  </button>
                  {consents.connected_banks === 0 && <span className="hint">The client has no account at any of the 12 banks.</span>}
                </div>
              )}
            </section>
          )}

          {job && !showResult && job.status !== 'failed' && (
            <section className="card enter">
              <h2>Collecting data</h2>
              <Progress job={job} onFinished={onFinished} />
            </section>
          )}

          {job?.status === 'failed' && (
            <section className="card">
              <p className="error" role="alert">The estimate failed: {job.error}</p>
            </section>
          )}

          {showResult && <Result result={job.result} />}
        </main>
      </div>
    </div>
  )
}
