#!/usr/bin/env python3
"""Poll the public main branch, verify ARM64 images, and replace only this demo.

Installed outside the watched checkout. Python standard library only. Does not
create commits, push, merge, modify lockfiles, or change application calculations.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path("/home/ubuntu/IEM_NBG_OpenBanking")
STATE = Path("/srv/income-check/deployment")
LIB = Path("/usr/local/lib/income-check")
REMOTE = "https://github.com/konstantine25b/IEM_NBG_OpenBanking.git"
URL = "https://incomecheck-demo.duckdns.org"
MODEL_SHA = "faa60238369de94c9ff19963243bc9ede32224b9d363bfb3bf66701705e8ce5b"
NAME = "income-check"
OWNER = "org.iem-nbg.autodeploy"
CANDIDATE = "income-check-autodeploy-candidate"
REGRESSION = "income-check-autodeploy-regression"


def atomic_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class Deployer:
    def __init__(self):
        self.logfile = None
        self.logpath = None

    def log(self, message):
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)
        if self.logfile:
            self.logfile.write(message + "\n")
            self.logfile.flush()

    def run(self, *args, timeout=120, record=False):
        env = os.environ | {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes"}
        if record:
            result = subprocess.run(args, cwd=REPO, env=env, timeout=timeout,
                                    stdout=self.logfile, stderr=subprocess.STDOUT, check=False)
            if result.returncode:
                raise RuntimeError(f"Command exited {result.returncode}: {args[0:3]}; see {self.logpath}")
            return ""
        result = subprocess.run(args, cwd=REPO, env=env, timeout=timeout, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            raise RuntimeError(f"Command exited {result.returncode}: {args[0:3]}: {result.stdout[-2000:]}")
        return result.stdout.strip()

    def container(self, name=NAME):
        result = subprocess.run(["docker", "inspect", name], text=True, capture_output=True)
        return json.loads(result.stdout)[0] if result.returncode == 0 else None

    def remove_owned(self, name):
        container = self.container(name)
        if container:
            if container["Config"].get("Labels", {}).get(OWNER) != "true":
                raise RuntimeError(f"Refusing to remove an unrelated container: {name}")
            self.run("docker", "rm", "-f", name)

    def audit(self, action="snapshot", *args):
        return json.loads(self.run("sudo", "-n", "python3", str(LIB / "audit.py"), action, *map(str, args)))

    def check_audit(self, before):
        after = self.audit("snapshot", before["last_id"])
        if after != before:
            raise RuntimeError("Previously persisted audit rows changed")

    def wait_ready(self, name, port, timeout=180, *, allow_restart_history=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container = self.container(name)
            if (not container or not container["State"]["Running"]
                    or (container["RestartCount"] and not allow_restart_history)):
                raise RuntimeError(f"Container failed during startup: {name}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                    healthy = response.status == 200
                if healthy and container["State"].get("Health", {}).get("Status") == "healthy":
                    return
            except OSError:
                pass
            time.sleep(1)
        raise RuntimeError(f"Readiness timed out: {name}")

    def smoke(self, url, *, all_clients=False, proxy=False):
        command = ["python3", str(LIB / "verify.py"), url]
        if all_clients:
            command.append("--all-clients")
        if proxy:
            command.append("--local-proxy")
        self.run(*command, timeout=600, record=True)

    def create(self, image, name, port, *, persistent, revision):
        command = ["docker", "run", "-d", "--name", name, "--platform", "linux/arm64",
                   "--label", f"{OWNER}=true", "--label", f"{OWNER}.revision={revision}",
                   "--stop-timeout", "30", "--memory", "2g", "--memory-swap", "2g",
                   "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
                   "-p", f"127.0.0.1:{port}:10000", "-e", "FORWARDED_ALLOW_IPS=172.17.0.1"]
        if persistent:
            command += ["--restart", "unless-stopped", "--mount",
                        "type=bind,src=/srv/income-check/runtime,dst=/app/.runtime"]
        else:
            command += ["--restart", "no", "--tmpfs", "/app/.runtime:rw,uid=10001,gid=10001,mode=0700,size=32m"]
        self.run(*command, image)

    def verify_image(self, image, revision):
        value = json.loads(self.run("docker", "image", "inspect", image))[0]
        labels = value["Config"].get("Labels", {})
        if value["Os"] != "linux" or value["Architecture"] != "arm64":
            raise RuntimeError("Expected a native linux/arm64 image")
        if labels.get("org.opencontainers.image.revision") != revision or labels.get("org.opencontainers.image.ref.name") != "main":
            raise RuntimeError("Image does not record the requested main revision")

    def verify_candidate(self, image, revision):
        try:
            self.create(image, CANDIDATE, 10001, persistent=False, revision=revision)
            self.wait_ready(CANDIDATE, 10001)
            self.log("Verifying all 20 demo customers privately")
            self.smoke("http://127.0.0.1:10001", all_clients=True)
        finally:
            self.remove_owned(CANDIDATE)

    def rollback(self, transaction):
        self.log("Restoring the previous application container")
        current = self.container()
        if current and current["Id"] != transaction["old_id"]:
            self.remove_owned(NAME)
        backup = self.container(transaction["backup"])
        if backup:
            if backup["Id"] != transaction["old_id"]:
                raise RuntimeError("Rollback container identity changed; manual inspection required")
            self.run("docker", "rename", transaction["backup"], NAME)
        if not self.container() or self.container()["Id"] != transaction["old_id"]:
            raise RuntimeError("Previous application container is missing")
        if transaction.get("audit"):
            try:
                self.check_audit(transaction["audit"])
            except Exception:
                self.log("Preserving failed audit files and restoring the pre-deployment SQLite backup")
                self.audit("restore", transaction["audit_backup"])
                self.check_audit(transaction["audit"])
        self.run("docker", "start", NAME)
        self.wait_ready(NAME, 10000, allow_restart_history=True)
        (STATE / "transaction.json").unlink(missing_ok=True)

    def finish(self, transaction):
        backup = self.container(transaction["backup"])
        if backup:
            if backup["Id"] != transaction["old_id"] or backup["State"]["Running"]:
                raise RuntimeError("Previous container identity/state changed")
            self.run("docker", "rm", transaction["backup"])
        atomic_json(STATE / "last-success.json", {
            "commit": transaction["commit"], "image": transaction["image"],
            "previous_image": transaction["old_image"], "deployed_at": datetime.now(UTC).isoformat(),
            "url": URL, "audit_preserved": transaction["audit"], "log": str(self.logpath),
        })
        (STATE / "transaction.json").unlink(missing_ok=True)

    def recover(self):
        path = STATE / "transaction.json"
        if path.exists():
            transaction = json.loads(path.read_text())
            if transaction.get("verified") and self.container() and self.container()["Config"]["Image"] == transaction["image"]:
                self.wait_ready(NAME, 10000, allow_restart_history=True)
                self.finish(transaction)
            else:
                self.rollback(transaction)
        for name in (CANDIDATE, REGRESSION):
            self.remove_owned(name)

    def activate(self, image, revision):
        old = self.container()
        if not old or not old["State"]["Running"]:
            raise RuntimeError("Live application is stopped; refusing to override an intentional stop")
        if old["HostConfig"]["PortBindings"] != {"10000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "10000"}]}:
            raise RuntimeError("Live port bindings differ from the expected deployment")
        transaction = {"commit": revision, "image": image, "old_id": old["Id"],
                       "old_image": old["Config"]["Image"], "backup": f"income-check-rollback-{old['Id'][:12]}"}
        if self.container(transaction["backup"]):
            raise RuntimeError("Rollback name is already occupied")
        atomic_json(STATE / "transaction.json", transaction)
        try:
            self.log("Private checks passed; replacing the live application")
            self.run("docker", "stop", "--timeout", "30", NAME)
            if self.container()["State"]["ExitCode"] != 0:
                raise RuntimeError("Previous application did not shut down gracefully")
            transaction["audit"] = self.audit()
            transaction["audit_backup"] = self.audit("backup")["name"]
            atomic_json(STATE / "transaction.json", transaction)
            self.run("docker", "rename", NAME, transaction["backup"])
            self.create(image, NAME, 10000, persistent=True, revision=revision)
            self.wait_ready(NAME, 10000)
            self.check_audit(transaction["audit"])
            self.smoke(URL, proxy=True)
            self.check_audit(transaction["audit"])
            transaction["verified"] = True
            atomic_json(STATE / "transaction.json", transaction)
        except BaseException:
            # Do not allow a second SIGTERM to interrupt restoration of the old container.
            old_handler = signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                self.rollback(transaction)
            finally:
                signal.signal(signal.SIGTERM, old_handler)
            raise
        self.finish(transaction)

    def cleanup_images(self):
        keep = set()
        names = set()
        live = self.container()
        if live:
            names.add(live["Config"]["Image"])
        success_path = STATE / "last-success.json"
        if success_path.exists():
            success = json.loads(success_path.read_text())
            names.update((success["image"], success["previous_image"]))
        transaction_path = STATE / "transaction.json"
        if transaction_path.exists():
            names.add(json.loads(transaction_path.read_text())["old_image"])
        for name in names:
            keep.add(json.loads(self.run("docker", "image", "inspect", name))[0]["Id"])
        ids = self.run("docker", "image", "ls", "--filter", f"label={OWNER}=true", "--quiet").splitlines()
        for image_id in set(ids):
            details = json.loads(self.run("docker", "image", "inspect", image_id))[0]
            if details["Id"] not in keep:
                # No force: Docker retains images used by any other container.
                result = subprocess.run(["docker", "image", "rm", image_id], capture_output=True, text=True)
                if result.returncode:
                    self.log(f"Retained image still in use: {image_id}")
        active_backup = None
        if transaction_path.exists():
            active_backup = json.loads(transaction_path.read_text()).get("audit_backup")
        for pattern, limit in (("logs/*.log", 10), ("backups/audit-*.sqlite3", 5)):
            paths = sorted(STATE.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths[limit:]:
                if path.name != active_backup:
                    path.unlink()

    def deploy(self, force=False):
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "logs").mkdir(exist_ok=True)
        with (STATE / "deploy.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.log("A deployment is already running")
                return
            self.recover()
            if self.run("uname", "-m") != "aarch64":
                raise RuntimeError("Deployments must build on the native ARM64 VM")
            if self.run("git", "remote", "get-url", "origin") != REMOTE:
                raise RuntimeError("Unexpected Git origin")
            self.run("git", "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
            revision = self.run("git", "rev-parse", "origin/main")
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise RuntimeError("Unexpected main commit format")
            live = self.container()
            if not live or not live["State"]["Running"]:
                self.log("Application is explicitly stopped or absent; automatic deployment skipped")
                return
            deployed = live["Config"].get("Labels", {}).get("org.opencontainers.image.revision")
            if deployed == revision and not force:
                self.log(f"Up to date: main {revision}")
                return
            failed_path = STATE / "last-failure.json"
            if not force and failed_path.exists():
                failed = json.loads(failed_path.read_text())
                if failed.get("commit") == revision and time.time() - failed.get("time", 0) < 600:
                    self.log(f"Previous attempt failed; retrying {revision} after the 10-minute cooldown")
                    return
            if self.run("git", "status", "--porcelain"):
                raise RuntimeError("VM checkout has local edits; refusing to overwrite them")
            head = self.run("git", "rev-parse", "HEAD")
            self.run("git", "merge-base", "--is-ancestor", head, revision)
            if shutil.disk_usage(REPO).free < 8 * 1024**3:
                raise RuntimeError("Less than 8 GiB disk space available; live demo retained")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            self.logpath = STATE / "logs" / f"{stamp}-{revision[:12]}.log"
            self.logfile = self.logpath.open("w", buffering=1)
            image = f"income-check:auto-{revision}"
            tests = f"income-check:auto-tests-{revision}"
            try:
                # Fast-forward only, checked above; creates no commit or merge.
                self.run("git", "switch", "-C", "main", revision)
                self.run("git", "branch", "--set-upstream-to=origin/main", "main")
                model_hash = self.run("sha256sum", "income/models/income_model.joblib").split()[0]
                if model_hash != MODEL_SHA:
                    raise RuntimeError(f"Model checksum changed: {model_hash}; expected {MODEL_SHA}")
                self.log(f"Building native ARM64 main {revision}; log {self.logpath}")
                labels = ["--label", f"{OWNER}=true", "--label", f"org.opencontainers.image.revision={revision}",
                          "--label", "org.opencontainers.image.ref.name=main"]
                self.run("docker", "build", "--platform", "linux/arm64", *labels,
                         "--progress=plain", "-t", image, ".", timeout=1800, record=True)
                self.verify_image(image, revision)
                self.log("Running selected regressions; no training tests or dataset-generation fixtures")
                self.run("docker", "build", "--platform", "linux/arm64", "--target", "test", *labels,
                         "--progress=plain", "-t", tests, ".", timeout=1800, record=True)
                self.run("docker", "run", "--rm", "--name", REGRESSION, "--label", f"{OWNER}=true",
                         "--platform", "linux/arm64", "--memory", "3g", "--memory-swap", "3g", tests,
                         "python", "-m", "pytest", "tests/test_demo_workspace.py", "tests/test_production.py",
                         "-p", "no:cacheprovider", timeout=600, record=True)
                self.verify_candidate(image, revision)
                if self.run("git", "rev-parse", "HEAD") != revision or self.run("git", "status", "--porcelain"):
                    raise RuntimeError("VM source changed during verification; live deployment retained")
                self.run("git", "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
                if self.run("git", "rev-parse", "origin/main") != revision:
                    self.log("Main advanced during verification; the next poll will build the latest commit")
                    return
                self.activate(image, revision)
                failed_path.unlink(missing_ok=True)
                self.log(f"Deployed main {revision}: {URL}")
            except BaseException as error:
                atomic_json(failed_path, {"commit": revision, "time": time.time(), "error": str(error),
                                          "log": str(self.logpath)})
                self.log(f"Deployment failed: {error}")
                raise
            finally:
                for name in (CANDIDATE, REGRESSION):
                    self.remove_owned(name)
                try:
                    self.cleanup_images()
                except Exception as cleanup_error:
                    self.log(f"Deployment image/log cleanup deferred: {cleanup_error}")
                self.logfile.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild and verify even when main is already deployed")
    args = parser.parse_args()

    def interrupted(signum, _frame):
        raise InterruptedError(f"Deployment interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        Deployer().deploy(force=args.force)
    except Exception as error:
        print(f"Automatic deployment stopped: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
