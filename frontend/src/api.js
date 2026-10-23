// Thin client for the income API (API/service.py). Requests go to /api, which Vite proxies to :9000.

async function call(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const error = new Error(data?.text || data?.error || (typeof data?.detail === 'string' ? data.detail : null) || `Request failed (${res.status})`)
    error.status = res.status
    throw error
  }
  return data
}

// Phase 1: one click, all banks + RS + Creditinfo.
export const connectAll = (personalId) => call('POST', '/v1/consents', { personal_id: personalId })

// Phase 2: one call, returns a job id right away.
export const startEstimate = (consentSetId, loanRequest, demoFailure = 'none') =>
  call('POST', '/v1/income-estimates', { consent_set_id: consentSetId, loan_request: loanRequest, demo_failure: demoFailure })

export const getEstimate = (jobId) => call('GET', `/v1/income-estimates/${jobId}`)

// Ten hand-picked demo clients (API/pick_demo_clients.py).
export const getDemoClients = () => call('GET', '/v1/demo/clients')

// The 12 banks + RS + Creditinfo that a consent request goes to.
export const getSources = () => call('GET', '/v1/sources')

export const getAudit = (caseId) => call('GET', `/v1/cases/${caseId}/audit`)
export const reviewCase = (caseId, decision, reason, acknowledge) =>
  call('POST', `/v1/cases/${caseId}/review`, { decision, reason, acknowledge_uncertainty: acknowledge })
export const startBenchmark = (caseId) => call('POST', `/v1/cases/${caseId}/benchmark/start`)
export const finishBenchmark = (caseId, paymentCount, totalGel) =>
  call('POST', `/v1/cases/${caseId}/benchmark/finish`, { payment_count: paymentCount, total_gel: totalGel })
export const getEvaluation = () => call('GET', '/v1/evaluation')
export const runEvaluation = () => call('POST', '/v1/evaluation')
