Automatic deployment serves **https://incomecheck-demo.duckdns.org** from the public repository's `main` branch.

The Oracle VM checks GitHub about every minute using `income-check-deploy.timer`. No GitHub credentials, Actions runner, webhook listener, new public ports, commits, or pushes are needed. Existing SSH restrictions remain intact. An update usually takes a few more minutes for its native build and verification.

For each new main commit, the installed worker:

1. Checks the origin, native ARM64 host, available disk, and clean source checkout. It accepts fast-forward main updates and never creates commits or merges.
2. Builds the existing Dockerfile natively for `linux/arm64`, using `npm ci` and locked Python dependencies. It refuses a changed model checksum.
3. Builds the existing test target and runs only `tests/test_demo_workspace.py` and `tests/test_production.py`; it does not invoke training tests or dataset-generation fixtures.
4. Starts a disposable candidate on **127.0.0.1:10001**, with all three services inside that container and temporary audit storage. It runs all 20 existing customer workflows, exact C50811 inference/SHAP/reference checks, consent/review/audit, unrelated-origin rejection, and both outage scenarios.
5. Confirms main has not advanced during testing. If a newer commit exists, the next poll tests that revision before any cutover.
6. Stops the live container gracefully, verifies and backs up SQLite, and retains the old container for rollback. It starts the verified image under the existing `income-check` name, with one API worker, **127.0.0.1:10000**, the same hard 2 GiB limit, restart policy, log rotation, and persistent `/srv/income-check/runtime` mount.
7. Verifies old audit rows and a complete HTTPS workflow through local Nginx, retaining certificate verification, SNI, Host, Origin, and forwarded headers. It removes the stopped previous container only after success.

Builds and private tests run while the website remains available. Cutover briefly interrupts the demo and clears its in-memory assessments and consents. Audit storage persists. A failed build/private test leaves the current site running. A failed startup or post-cutover check restores the old container. If an update changes existing audit data, the failed database files are preserved and the pre-deployment SQLite backup is restored. Interrupted cutovers are recovered on the next run.

The reference smoke tool is installed outside the watched checkout so future source changes cannot silently weaken these deployment gates. The model checksum remains `faa60238369de94c9ff19963243bc9ede32224b9d363bfb3bf66701705e8ce5b`. An intentional change to that reference requires an explicit update to deployment verification.

No-change polls do not rebuild or restart the app. Failed commits have a 10-minute retry cooldown; a different new commit is eligible immediately. A manually stopped application stays stopped. Uncommitted VM edits, non-fast-forward main history, or less than 8 GiB free disk stop deployment without overwriting source or upgrading the server. The worker removes only containers/images it owns, keeps current/previous successful images, 10 run logs, and five SQLite backups. Quarantined failed audit files remain for review. It never runs a global Docker prune or changes firewall rules.

Host installation paths:

| Purpose | Path |
|---|---|
| Deployment worker and trusted smoke | `/usr/local/lib/income-check/` |
| Timer / service | `/etc/systemd/system/income-check-deploy.timer` and `.service` |
| State, logs, SQLite backups | `/srv/income-check/deployment/` |
| Public source checkout | `/home/ubuntu/IEM_NBG_OpenBanking` |
| Persistent live audit | `/srv/income-check/runtime/audit.sqlite3` |

Install or update these tools from a staging directory on the VM as `ubuntu`, with the checkout at the reviewed commit:

```sh
sh install.sh
python3 /usr/local/lib/income-check/autodeploy.py --force
sudo systemctl enable --now income-check-deploy.timer
```

The tools are deliberately installed outside the application checkout and do not update themselves from future pushes. Keep this directory's reviewed sources with your project; committing them is optional for operation and was not performed during setup.

Inspect, trigger, or pause automation after SSH login:

```sh
# Poll schedule and deployment log
systemctl list-timers income-check-deploy.timer
journalctl -u income-check-deploy.service -n 100 --no-pager
cat /srv/income-check/deployment/last-success.json

# Check main now; unchanged commits are skipped
sudo systemctl start income-check-deploy.service

# Rebuild/retry the current main revision explicitly
python3 /usr/local/lib/income-check/autodeploy.py --force

# Pause / resume future automatic deployments
sudo systemctl disable --now income-check-deploy.timer
sudo systemctl enable --now income-check-deploy.timer
```

The timer controls future runs. To interrupt an in-progress deployment as well, stop `income-check-deploy.service`; its transaction record allows recovery. To take the demo offline intentionally, disable the timer, then `docker stop --timeout 30 income-check`. Start the container and re-enable the timer to resume.

The site remains an unauthenticated synthetic prototype. Consent and financial sources are mocks; audit persistence does not preserve assessments or consents. SOI04 is remittance income, the precision score is not a probability of correctness, and real-world accuracy/production banking security remain unestablished.

Verified on 25 October 2026:

- Full deployment rehearsal on native ARM64 for main commit `05a8503e904c3a7584ba896badf21e25f014485c`: existing Dockerfile and lockfiles, 36 selected regressions, all 20 private customer workflows, and the HTTPS workflow through Nginx passed.
- A deliberately broken startup exited 42. The actual deployment worker restored the exact previous healthy container and preserved all 2,686 pre-existing audit rows with valid hash chains.
- All 10 deployment safety regressions passed locally and on the VM, including no-change polls, intentional stops, failed candidate checks, failed cutovers, interrupted transaction recovery, audit backup restoration, and an empty audit prefix.
- The timer is enabled and active. A real scheduled poll completed with exit code 0 and left the unchanged main container running under the same ID.
- Public HTTPS smoke and actual Chrome consent/inference/SHAP/review/audit workflows passed afterward, with zero browser errors and exact C50811 reference results. TLS certificate verification passed externally; ports 8000, 8001, 10000, and 10001 were unreachable from the Mac.
- Final application working memory was about 858 MiB under the retained hard 2 GiB limit. The native image, UID 10001, one public worker, private bindings, persistent mount, origin checks, model checksum, and application lockfiles remained intact.

Initial setup verified the timer's detection/no-change path and the complete deployment pipeline against current main without creating a test commit or push. Live rollback verification uses Docker's recorded `die` event to check the failed startup's exit code.

Current rollout details are recorded in `/srv/income-check/deployment/last-success.json`; timer, rollback, and combined setup evidence are in `timer-verification.json`, `rollback-verification.json`, and `verification.json` beside it. This runbook is also installed as `/srv/income-check/AUTODEPLOYMENT.md`.
