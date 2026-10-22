import { useCallback, useEffect, useRef, useState } from 'react'
import { connectAll, getDemoClients, getEstimate, startEstimate } from './api.js'
import ConsentTable from './components/ConsentTable.jsx'
import Progress from './components/Progress.jsx'
import Result from './components/Result.jsx'
import { pace, wait } from './hooks.js'
import './app.css'

const POLL_MS = 700

export default function App() {
  const [personalId, setPersonalId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [consents, setConsents] = useState(null)
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [demoClients, setDemoClients] = useState([])
  const [revealed, setRevealed] = useState(false)
  const timer = useRef(null)

  useEffect(() => {
    getDemoClients().then(setDemoClients).catch(() => setDemoClients([]))
    return () => clearTimeout(timer.current)
  }, [])

  const reset = () => {
    clearTimeout(timer.current)
    setConsents(null)
    setJob(null)
    setRevealed(false)
    setError(null)
  }

  const onConnect = async (event) => {
    event.preventDefault()
    reset()
    setConnecting(true)
    try {
      // the call answers in a blink; hold the spinner a moment so the click visibly does something
      const [result] = await Promise.all([connectAll(personalId.trim()), wait(pace(900))])
      setConsents(result)
    } catch (e) {
      setError(e.status === 404 ? 'No bank knows this personal ID.' : e.message)
    } finally {
      setConnecting(false)
    }
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

  const onFinished = useCallback(() => setRevealed(true), [])

  const onEstimate = async () => {
    setError(null)
    setRevealed(false)
    setJob({ status: 'queued', stage: 'queued', progress: [] })
    try {
      const { job_id } = await startEstimate(consents.consent_set_id)
      poll(job_id)
    } catch (e) {
      setJob(null)
      setError(e.message)
    }
  }

  const running = job && job.status !== 'done' && job.status !== 'failed'

  return (
    <main className="page">
      <header className="top">
        <h1>Income check</h1>
        <p>Estimate a client&apos;s sustainable monthly income from their open-banking data, RS and Creditinfo.</p>
      </header>

      <section className="card">
        <h2><span className="num">1</span> Connect the client&apos;s banks</h2>
        <form className="row" onSubmit={onConnect}>
          <input
            value={personalId}
            onChange={(e) => setPersonalId(e.target.value)}
            placeholder="11-digit personal ID, e.g. 09754421550"
            aria-label="Personal ID"
            disabled={connecting || running}
          />
          <button type="submit" disabled={!personalId.trim() || connecting || running}>
            {connecting ? 'Connecting…' : 'Connect all banks'}
          </button>
        </form>
        {demoClients.length > 0 && (
          <div className="demo">
            <p className="hint">Demo clients (ten different kinds of person, none used to train the model):</p>
            <div className="demo-grid">
              {demoClients.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={`demo-card ${personalId === c.personal_id ? 'active' : ''}`}
                  onClick={() => setPersonalId(c.personal_id)}
                  disabled={connecting || running}
                  title={c.story}
                >
                  <strong>{c.title}</strong>
                  <span>{c.facts}</span>
                  <span className="tags">
                    {c.banks.map((b) => <em key={b} className="tag">{b}</em>)}
                    {c.has_deposit && <em className="tag deposit">deposit</em>}
                  </span>
                  <code>{c.personal_id}</code>
                </button>
              ))}
            </div>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        {consents && <ConsentTable consents={consents} />}
        {consents && !job && (
          <div className="actions">
            <button type="button" onClick={onEstimate} disabled={consents.connected_banks === 0}>
              Estimate income
            </button>
            {consents.connected_banks === 0 && <span className="hint">The client has no account at any of the 12 banks.</span>}
          </div>
        )}
      </section>

      {job && (
        <section className="card enter">
          <h2><span className="num">2</span> Collecting data</h2>
          <Progress job={job} onFinished={onFinished} />
        </section>
      )}

      {job?.status === 'done' && revealed && (
        <section className="card enter">
          <h2><span className="num">3</span> Result</h2>
          <Result result={job.result} />
        </section>
      )}

      {job?.status === 'failed' && (
        <section className="card">
          <p className="error" role="alert">The estimate failed: {job.error}</p>
        </section>
      )}
    </main>
  )
}
