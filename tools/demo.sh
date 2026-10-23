#!/usr/bin/env bash
# Start the whole income-check demo: three APIs and the web page. Ctrl-C stops all of them.
#   make demo        (needs `uv sync` once, and `make frontend-setup` once)
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null; then echo "npm not found: install Node.js first (brew install node)"; exit 1; fi
[ -d frontend/node_modules ] || { echo "run 'make frontend-setup' first"; exit 1; }

pids=()
trap 'echo; echo "stopping..."; kill "${pids[@]}" 2>/dev/null || true' EXIT INT TERM

uv run python API/mock_banks.py --port 8000 & pids+=($!)
uv run python API/mock_registries.py --port 8001 & pids+=($!)
echo "waiting for the bank mock to plan 100,000 customers..."
until curl -sf localhost:8000/_mock/clock >/dev/null; do sleep 1; done
uv run python API/service.py --port 9000 & pids+=($!)
(cd frontend && npm run dev -- --port 5173) & pids+=($!)

until curl -sf localhost:9000/health >/dev/null; do sleep 1; done
echo
echo "  page:     http://localhost:5173"
echo "  Swagger:  http://localhost:9000/docs"
echo
wait
