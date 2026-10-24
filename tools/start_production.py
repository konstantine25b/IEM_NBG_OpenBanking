"""Supervise the three existing FastAPI applications in one Linux container."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    port = int(os.environ.get("PORT", "10000"))
    startup_timeout = float(os.environ.get("STARTUP_TIMEOUT", "180"))
    shutdown_timeout = float(os.environ.get("SHUTDOWN_TIMEOUT", "25"))
    if not 1 <= port <= 65535 or port in (8000, 8001):
        raise ValueError("PORT must be 1..65535 and different from internal ports 8000/8001")
    if startup_timeout <= 0 or shutdown_timeout <= 0:
        raise ValueError("Startup and shutdown timeouts must be positive")
    stopping = False
    children: list[tuple[str, subprocess.Popen]] = []

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def check_children():
        for name, process in children:
            code = process.poll()
            if code is not None:
                raise RuntimeError(f"Critical service {name} exited with status {code}")

    def start(name: str, args: list[str]):
        check_children()
        process = subprocess.Popen([sys.executable, *args], cwd=ROOT, start_new_session=True)
        children.append((name, process))
        print(f"Started {name} (PID {process.pid})", flush=True)

    def ready(url: str):
        deadline = time.monotonic() + startup_timeout
        while not stopping:
            check_children()
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Dependency did not become ready: {url}")
            time.sleep(0.2)

    def signal_child(process: subprocess.Popen, sig: int):
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, sig)

    try:
        start("banks", ["API/mock_banks.py", "--host", "127.0.0.1", "--port", "8000"])
        start("registries", ["API/mock_registries.py", "--host", "127.0.0.1", "--port", "8001"])
        ready("http://127.0.0.1:8000/_mock/clock")
        ready("http://127.0.0.1:8001/openapi.json")
        if not stopping:
            start("income-api", [
                "-m", "uvicorn", "API.production:create_app", "--factory", "--host", "0.0.0.0",
                "--port", str(port), "--workers", "1", "--proxy-headers",
                "--forwarded-allow-ips", os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                "--timeout-graceful-shutdown", str(max(1, int(shutdown_timeout) - 2)),
            ])
            ready(f"http://127.0.0.1:{port}/health")
        if not stopping:
            print(f"All services ready; public port {port}", flush=True)
        while not stopping:
            check_children()
            time.sleep(0.2)
        return 0
    except Exception as error:
        print(f"Production supervisor failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        # Stop the public API first and give its active inference jobs time to finish while mocks still run.
        deadline = time.monotonic() + shutdown_timeout
        for name, process in reversed(children):
            signal_child(process, signal.SIGTERM)
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                print(f"Shutdown timeout: killing {name}", file=sys.stderr, flush=True)
                signal_child(process, signal.SIGKILL)
                process.wait()
        print("All services stopped", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
