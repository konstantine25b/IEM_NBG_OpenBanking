// Thin client for the income API (API/service.py). Requests go to /api, which Vite proxies to :9000.

async function call(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const error = new Error(data?.text || data?.error || `Request failed (${res.status})`)
    error.status = res.status
    throw error
  }
  return data
}

// Phase 1: one click, all banks + RS + Creditinfo.
export const connectAll = (personalId) => call('POST', '/v1/consents', { personal_id: personalId })

// Phase 2: one call, returns a job id right away.
export const startEstimate = (consentSetId, loanRequest) =>
  call('POST', '/v1/income-estimates', { consent_set_id: consentSetId, loan_request: loanRequest })

export const getEstimate = (jobId) => call('GET', `/v1/income-estimates/${jobId}`)

// Ten hand-picked demo clients (API/pick_demo_clients.py).
export const getDemoClients = () => call('GET', '/v1/demo/clients')
