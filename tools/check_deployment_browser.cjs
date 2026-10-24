// Optional browser smoke test against the built Docker/Render application.
// npm install --prefix .runtime/deploy-browser --no-package-lock --ignore-scripts playwright@1.58.2
// node tools/check_deployment_browser.cjs http://127.0.0.1:18080
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const { chromium } = require(require.resolve('playwright', { paths: [root, path.join(root, '.runtime/deploy-browser')] }))

async function main() {
  const url = (process.argv[2] || 'http://127.0.0.1:18080').replace(/\/$/, '')
  const browser = await chromium.launch({ headless: true,
    ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  const errors = []
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
    page.on('pageerror', error => errors.push(error.message))
    page.on('response', response => {
      if (response.status() >= 400 && response.url().startsWith(url)) errors.push(`${response.status()} ${response.url()}`)
    })
    await page.goto(url, { waitUntil: 'networkidle' })
    await page.getByRole('button').filter({ hasText: 'C50811' }).click()
    await page.getByRole('button', { name: 'Connect all banks', exact: true }).click()
    await page.getByText('Consent valid', { exact: false }).first().waitFor()
    const created = page.waitForResponse(response => response.request().method() === 'POST'
      && response.url().endsWith('/api/v1/income-estimates'))
    await page.getByRole('button', { name: 'Estimate income', exact: true }).click()
    const jobId = (await (await created).json()).job_id
    await page.getByRole('heading', { name: 'How sure is this?', exact: true }).waitFor({ timeout: 120000 })
    const job = await (await page.request.get(`${url}/api/v1/income-estimates/${jobId}`)).json()
    assert.deepEqual(job.result.income, { p10: 1746, p50: 1878, p90: 2104, currency: 'GEL', period: 'monthly' })
    await page.getByText('1,878', { exact: true }).first().waitFor()
    const evidence = page.getByRole('heading', { name: 'Underlying transaction evidence', exact: true }).locator('..')
    assert.equal(await evidence.getByLabel('Activity filter').inputValue(), 'all')
    await evidence.getByLabel('Activity filter').selectOption('service')
    await evidence.getByText('Service payment', { exact: true }).first().waitFor()
    await evidence.getByLabel('Activity filter').selectOption('excluded')
    await evidence.locator('tbody').getByText('Excluded activity', { exact: false }).first().waitFor()
    await evidence.getByLabel('Evidence source').selectOption('rs')
    await evidence.getByText('SOI04', { exact: true }).waitFor()
    await evidence.getByLabel('Evidence source').selectOption('creditinfo')
    await evidence.getByText('Existing contracts as debtor', { exact: true }).waitFor()
    await evidence.getByLabel('Evidence source').selectOption('openbanking')
    await evidence.getByLabel('Activity filter').selectOption('all')
    await page.getByRole('button', { name: 'Show all factors', exact: true }).click()
    await page.locator('.drivers details').first().locator('summary').click()
    await page.getByRole('button', { name: 'Request more documents', exact: true }).click()
    await page.getByText('Write a reason of at least 10 characters first.', { exact: false }).waitFor()
    await page.locator('.officer textarea').fill('Deployment test: reviewed the uncertainty and financial evidence.')
    await page.getByLabel('I reviewed the uncertainty and the evidence').check()
    await page.getByRole('button', { name: 'Request more documents', exact: true }).click()
    await page.getByText('Decision: More documents requested', { exact: true }).waitFor()
    await page.getByText('officer_decision', { exact: true }).waitFor()
    await page.getByText('Hash chain and case result verified', { exact: true }).waitFor()
    fs.mkdirSync(path.join(root, '.runtime/deployment'), { recursive: true })
    await page.getByRole('heading', { name: 'How sure is this?', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(root, '.runtime/deployment/browser.png') })
    assert.deepEqual(errors, [], `Browser errors: ${errors.join('; ')}`)
    console.log(JSON.stringify({ passed: true, job_id: jobId, income: job.result.income,
      checks: ['compiled React', 'consent POST', 'estimate POST', 'source evidence filters', 'SHAP expansion',
        'officer validation', 'review POST', 'audit refresh'], browser_errors: errors }, null, 2))
  } finally {
    await browser.close()
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
