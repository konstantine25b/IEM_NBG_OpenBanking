"""Start the four local demo services, or validate the environment with --check."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="validate runtimes, data and the committed model without starting servers"
    )
    args = parser.parse_args()
    if sys.version_info < (3, 12):  # noqa: UP036 -- this entry point can be invoked with a system Python
        raise SystemExit("Use Python 3.12+: uv run python tools/run_demo.py")
    missing = [
        name
        for name in ("fastapi", "uvicorn", "httpx", "pandas", "pyarrow", "xgboost")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise SystemExit("Run uv sync first. Missing: " + ", ".join(missing))
    local_node = ROOT / "frontend/node_modules/node/bin/node.exe"
    node = str(local_node) if local_node.exists() else shutil.which("node")
    if not node:
        raise SystemExit("Install Node.js 22.12+ and run npm ci in frontend/.")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    major, minor = [int(v) for v in version.split(".")[:2]]
    if not (major == 20 and minor >= 19 or major == 22 and minor >= 12 or major >= 23):
        raise SystemExit(f"Node {version} is too old. Use Node 22.12+.")
    if not (ROOT / "frontend/node_modules/vite/bin/vite.js").exists():
        raise SystemExit("Run npm ci in frontend/ first.")
    for name in (
        "openbanking_mock/output/dataset/MANIFEST.json",
        "openbanking_mock/output/dataset/truth/customers/part-00203.parquet",
        "nbg_synthetic_data/income_dataset.csv",
    ):
        if not (ROOT / name).exists():
            raise SystemExit(f"Required existing synthetic data missing: {name}")
    sys.path.insert(0, str(ROOT / "income"))
    from income_predictor import IncomeModel

    model = IncomeModel.load()
    print(
        f"Python {sys.version.split()[0]}, Node {version}; committed model loaded ({model.booster.get_booster().num_features()} features)."
    )
    if args.check:
        print("Demo setup checks passed.")
        return 0
    services = [
        ("banks", 8000, [sys.executable, "-u", "API/mock_banks.py", "--port", "8000"], ROOT),
        ("registries", 8001, [sys.executable, "-u", "API/mock_registries.py", "--port", "8001"], ROOT),
        ("income-api", 9000, [sys.executable, "-u", "API/service.py", "--port", "9000"], ROOT),
        (
            "frontend",
            5173,
            [node, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
            ROOT / "frontend",
        ),
    ]
    for name, port, _, _ in services:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(
                    f"Port {port} is already in use ({name}). Stop the previous local demo before starting this launcher."
                )
    logs = ROOT / ".runtime/logs"
    logs.mkdir(parents=True, exist_ok=True)
    processes, files = [], []
    try:
        for name, _, command, cwd in services:
            log = (logs / f"{name}.log").open("a", encoding="utf-8")
            files.append(log)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("A demo process exited. See .runtime/logs/.")
            try:
                with urllib.request.urlopen("http://127.0.0.1:9000/health", timeout=1) as response:
                    if json.load(response).get("demo_version") == "case-review-v1":
                        with urllib.request.urlopen("http://127.0.0.1:5173", timeout=1):
                            break
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Startup timed out. See .runtime/logs/.")
        print("Open http://127.0.0.1:5173 — local synthetic demo. Press Ctrl+C to stop.", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                else:
                    process.terminate()
        for log in files:
            log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
