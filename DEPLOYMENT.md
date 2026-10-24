# One-container deployment on Render

The root Dockerfile builds the React/Vite frontend and runs the existing income API, 12-bank mock application, and RS/Creditinfo mock application in one container. One Render Web Service serves the website and API at one HTTPS URL. Vite is used only at build time.

This packages the existing synthetic prototype for hosting. It does not add bank authentication, train a model, produce a new dataset, or change financial calculations. The mocks retain their existing deterministic, on-demand statement behavior.

## Memory requirement: do not select Render Free

**The current application does not fit in 512 MiB.** A locally tested Docker container with `--memory 512m --memory-swap 512m` was OOM-killed during startup: the bank mock exited with signal 9, and the supervisor stopped the registry service and exited with status 1. Docker reported `OOMKilled: true`.

Use a **paid instance with at least 2 GiB RAM**, one instance/replica. The complete workflow was tested locally with a hard 2 GiB limit. This is a bounded synthetic-demo check, not a sustained-load guarantee. Existing caches, in-memory assessments and consent records can grow with usage; monitor Render's memory metrics.

Measured on Linux/amd64 Docker Desktop, 24 October 2026:

| Measurement | Result |
|---|---|
| Idle Docker working memory after startup | About 820 MiB |
| Working memory after all 20 demo customers and two outage scenarios | About 1.11 GiB |
| Cgroup peak after that workflow | 1,496,436,736 bytes, about 1.39 GiB; includes charged filesystem cache |
| Hard 512 MiB test | Failed at startup, OOM-killed |
| Hard 2 GiB test | Complete API workflows passed |

Docker working memory discounts inactive file cache; the cgroup peak includes charged cache and is useful when choosing a memory limit. No claim is made that a 1 GiB service is sufficient. Choose a Render instance by its advertised RAM rather than assuming every paid tier has enough memory.

Render also [documents Free-instance idle spin-down and ephemeral storage](https://render.com/docs/free). Its 512 MB option is not a viable fallback for this image.

## Exact Render settings

1. In Render, choose **New → Web Service** and connect the repository/branch containing these changes. Use your fork if that is the repository you control. Render can connect a repository your account can access, subject to Git-provider authorization.
2. Configure the following:

| Setting | Value |
|---|---|
| Service type | Web Service |
| Language / Runtime | Docker |
| Branch | The branch containing this Dockerfile and production files |
| Root directory | Leave blank: repository root, **not** `frontend` or `API` |
| Dockerfile path | `./Dockerfile` |
| Docker build context | Repository root (`.`), if the dashboard exposes this field |
| Docker command / start-command override | Leave blank; use the image's `CMD` |
| Build/pre-deploy command overrides | Leave blank |
| Instance memory | At least **2 GiB**, paid |
| Instances / replicas | **1** |
| Health check path | **`/health`** |
| Auto deploy | Disable initially if you want deployment only after explicit review |

3. Choose the environment variables below. No frontend build variables, separate API URL, CORS wildcard, or external mock-service URL is required.
4. Create the service when ready. A successful startup log ends with `All services ready; public port 10000` (or your configured `PORT`).

The Docker build stages run `npm ci` and `npm run build`, then `uv sync --locked --no-dev --no-install-project --no-editable` with the existing `uv.lock`. The runtime contains the virtual environment, existing code/data/model and `frontend/dist`; it contains no Node/Vite server, development dependencies or research notebooks. uv is pinned to 0.13.5. Python and Node use the maintained Python 3.12 and Node 22 Debian Bookworm image tags.

Official references: [Docker on Render](https://render.com/docs/docker), [Web Services and port binding](https://render.com/docs/web-services), [health checks](https://render.com/docs/health-checks).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `10000` | Render-provided public listener. The supervisor binds the income API to `0.0.0.0:$PORT`. Do not set it to 8000 or 8001. |
| `STARTUP_TIMEOUT` | `180` | Seconds allowed for each dependency/API readiness check. |
| `SHUTDOWN_TIMEOUT` | `25` | Total graceful-shutdown budget before remaining children are killed. |
| `FORWARDED_ALLOW_IPS` | `*` in Docker | Trust Render's reverse-proxy headers so HTTPS origins work after TLS terminates at Render. Limit this to trusted proxy addresses on a different hosting platform. |
| `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` | `1` | Bound numerical-library thread proliferation. Model code/weights/formulas are unchanged. |
| `MPLBACKEND` | `Agg` | Headless rendering support for the existing EDA module imported by the pipeline. |
| `MPLCONFIGDIR` | `/tmp/matplotlib` | Writable non-root Matplotlib cache. |
| `PYTHONUNBUFFERED`, `PYTHONDONTWRITEBYTECODE` | `1` | Immediate logs and no runtime bytecode writes to source directories. |

Normally, leave all values at their defaults and let Render supply `PORT`. There are no production banking credentials: the application continues to use synthetic sources and automatic mock consent.

## Routing and internal services

| URL / listener | Behavior |
|---|---|
| `/` | Compiled React index |
| `/assets/*`, `/favicon.svg`, `/icons.svg` | Compiled/static frontend files |
| `/api/v1/*` | Frontend's existing GET and POST requests, dispatched to the original `/v1/*` routes |
| `/v1/*` | Existing direct API routes preserved |
| `/health`, `/api/health` | Existing API health response |
| `/docs`, `/redoc`, `/openapi.json` | Existing Swagger/ReDoc/OpenAPI preserved; `/api/docs` and `/api/openapi.json` also resolve |
| `127.0.0.1:8000` | Existing 12-bank mock application, container-local only |
| `127.0.0.1:8001` | Existing RS and Creditinfo mock application, container-local only |

`API/production.py` removes the `/api` prefix at the ASGI boundary, preserving HTTP method, JSON body, query string and status. It mounts static files after the existing API routes. Unknown API/asset requests return 404; they are not replaced with a fake success page. The frontend currently uses a single page, so no extra SPA-router fallback is required.

The existing write-origin check now also accepts the public application's own origin. This permits same-origin consent, estimate and review POSTs on Render or a custom domain while retaining the existing unrelated-origin rejection. Local Vite origins remain supported.

`tools/start_production.py` starts the two existing mock entrypoints on localhost and waits for their HTTP readiness before starting one Uvicorn income-API worker. It waits for public API readiness, monitors all child processes, treats even an unexpected zero exit as a critical failure, and exits nonzero after stopping surviving processes. On SIGTERM/SIGINT it drains the public application first, then stops the mocks, with a bounded shutdown timeout and forced termination as a final fallback. Logs go to stdout/stderr for Render.

## Audit persistence and existing limits

The application runs as UID 10001, not root. Its writable audit directory is **`/app/.runtime`**, containing `audit.sqlite3`.

- For a disposable demo, the container's writable layer is sufficient, but audit events disappear on a replacement/redeploy.
- To retain audit events, attach a Render persistent disk mounted at **`/app/.runtime`**. Ensure the mounted directory is writable by UID/GID 10001; image-directory ownership alone does not determine the permissions of a newly mounted disk. Verify with the first startup logs and an assessment/audit request.
- A disk preserves audit events only. Existing assessment snapshots, consents and mock sessions remain in memory and cannot be reopened after a restart. This deployment does not alter that behavior.
- Keep one replica/worker. Multiple independent workers would not share those in-memory records.

Render [persistent disks](https://render.com/docs/disks) require a paid service. This public application retains the prototype's existing unauthenticated synthetic workflow; deployment packaging does not establish production banking security or real-customer model validation.

## Build and test locally

Run from the repository root with Docker Desktop in Linux-container mode:

```sh
docker build -t income-check:render .
docker run -d --name income-check --memory 2g --memory-swap 2g -p 127.0.0.1:18080:10000 income-check:render
docker logs -f income-check
```

Once ready, open **http://127.0.0.1:18080/**. Only the public API port is published. Do not map mock ports 8000/8001.

The host-side smoke test uses Python's standard library; it creates synthetic assessments and a review record in the running application:

```sh
python tools/check_deployment.py http://127.0.0.1:18080 --all-clients
docker stats --no-stream income-check
docker exec income-check sh -c 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max'
```

It verifies frontend assets, direct/prefixed health and docs, twelve-bank discovery plus RS/CI, all 20 demo customers, C50811's unchanged predictions and SHAP contribution, required review acknowledgment, verified audit, partial RS outage and withheld bank-outage result. It also checks rejection of an unrelated write origin.

Run the pinned regression suite in the optional test image:

```sh
docker build --target test -t income-check:tests .
docker run --rm --memory 3g --memory-swap 3g income-check:tests
```

The test target includes locked development dependencies; the default production target does not. These selected tests do not call dataset generation or model training. Existing broader suites that generate miniature synthetic datasets are deliberately outside this deployment verification.

Optional real-browser check using installed Edge on Windows (on Linux, install a Playwright Chromium browser first):

```sh
npm install --prefix .runtime/deploy-browser --no-package-lock --ignore-scripts playwright@1.58.2
node tools/check_deployment_browser.cjs http://127.0.0.1:18080
```

The browser test exercises the compiled interface, consent, estimate, source filters, SHAP expansion, officer validation/review and audit refresh. Its screenshot goes to git-ignored `.runtime/deployment/browser.png`.

Stop the local container gracefully:

```sh
docker stop --timeout 30 income-check
docker rm income-check
```

The existing local development command `uv run python tools/run_demo.py` and Vite proxy remain unchanged. Docker's production entrypoint is separate.

## Deployment URL checks

After a real deployment, replace `<service>` with the Render-assigned service name:

```text
https://<service>.onrender.com/
https://<service>.onrender.com/health
https://<service>.onrender.com/api/health
https://<service>.onrender.com/docs
https://<service>.onrender.com/openapi.json
https://<service>.onrender.com/api/v1/demo/clients
```

Both health URLs should return `status: ok`, the model/data window should be unchanged, and the source catalog should contain 14 entries: twelve banks, RS and Creditinfo. Select C50811 in the website, connect, estimate and inspect SHAP. Its reference result remains P10 1,746 / P50 1,878 / P90 2,104 GEL with Low confidence. The service-payment evidence remains 76 receipts totaling 21,694.69 GEL. Verify a review and audit record.

For an automated post-deploy check:

```sh
python tools/check_deployment.py https://<service>.onrender.com
```

If POSTs return 403, verify that the browser Origin matches the public URL and that Render's forwarded HTTPS scheme is trusted. If the public application never becomes ready, inspect supervisor logs and memory first. A missing frontend build or model/data file is a deployment/build error; do not generate replacement financial data to hide it.

## Verification results

Completed locally on 24 October 2026:

- Production multi-stage Docker build succeeded using the unchanged Python and npm lockfiles.
- 36 selected regression tests passed (existing demo-workspace tests plus production-routing tests). One upstream Starlette/httpx deprecation warning was emitted.
- HTTP end-to-end smoke checks passed for all 20 existing customers, plus actual RS and bank failure scenarios.
- C50811 predictions, confidence score, service receipts and +5 GEL RS-remittance SHAP contribution matched the existing reference.
- Model artifact SHA-256 remained `faa60238369de94c9ff19963243bc9ede32224b9d363bfb3bf66701705e8ce5b`.
- Render Free memory compatibility was disproved by the hard-limit OOM test above.
- Real headless Edge browser check passed with zero browser errors: compiled React, consent/estimate POSTs, source filters, SHAP expansion, required officer fields, review POST and audit refresh.
- The final image also passed the HTTP workflow on a non-default `PORT=12000`, published only at localhost port 18081.
- A same-origin consent POST with forwarded HTTPS headers returned 201, verifying the Render TLS-proxy configuration. An unrelated origin was rejected with 403.
- Deliberately killing the bank mock caused the supervisor to stop the public API and registry mock and exit with status 1; this intentional failure was not an OOM.
- A normal Docker SIGTERM stop of the final image exited with status 0 and shut down all three child processes.
- Runtime UID was 10001; Node and pytest were absent from the production image. The new production Python modules and smoke-test script passed the pinned Ruff checks.

The original service module has a pre-existing Ruff import-order finding outside the changed origin-check lines; no unrelated import reformat was made. No Render service has been deployed by this work. Temporary test containers were stopped after verification.
