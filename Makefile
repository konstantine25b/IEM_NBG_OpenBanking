# Shortcuts for the EDA project.  Everything runs through uv, so the right virtualenv is used automatically.
# Demo app (income check): API/ = three FastAPI services, frontend/ = React page.
.PHONY: demo frontend-setup api-banks api-registries api-service frontend help setup eda eda-fast eda-core eda-features notebook notebook-check synth synth-check synth-bank test test-all lint format clean

help:      ## list the commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'

setup:     ## create / update .venv from uv.lock
	uv sync

eda:       ## full EDA  -> reports/eda_report.html (a few minutes)
	uv run python eda/EDA.py

eda-fast:  ## 20k-row smoke run (about two minutes with the mock part)
	uv run python eda/EDA.py --fast

eda-core:  ## without the mock / synthetic bank data: sections 1-10 only (about 3.5 minutes)
	uv run python eda/EDA.py --no-mock

eda-features:  ## full run + reports/mock_customer_features.csv (one row per mock customer, synthetic)
	uv run python eda/EDA.py --export-mock-features

notebook:  ## (re)generate EDA.ipynb from EDA.py (keeps your edits safe: it refuses to overwrite changed cells)
	uv run python tools/make_notebook.py

notebook-check:  ## fail when EDA.ipynb is out of date with EDA.py
	uv run python tools/make_notebook.py --check

synth:     ## 100,000 new synthetic loans -> synthetic/loans (schema of nbg_synthetic_data/, gates in FIDELITY_REPORT.md; ~45 s)
	uv run python synthetic/synthgen.py

synth-check:  ## fidelity + privacy + train-synthetic/test-real on synthetic/loans
	uv run python synthetic/synthgen.py check --full

synth-bank:  ## bank API data for 2,000 synthetic loans through the unchanged openbanking_mock chain -> synthetic/bank_dataset
	uv run python synthetic/synthgen.py bank --customers 2000

test:      ## fast tests: statistics helpers + the whole script on a synthetic dataset
	uv run pytest

demo:      ## start the whole demo: bank mock :8000, RS+Creditinfo :8001, income API :9000, page :5173 (Ctrl-C stops)
	./tools/demo.sh

frontend-setup:  ## install the page's packages (once; needs Node.js)
	cd frontend && npm install

api-banks:  ## mock bank APIs for all 100,000 customers (:8000)
	uv run python API/mock_banks.py --port 8000

api-registries:  ## mock RS + Creditinfo (:8001)
	uv run python API/mock_registries.py --port 8001

api-service:  ## the income estimation API (:9000; Swagger at /docs); start after the two mocks
	uv run python API/service.py --port 9000

frontend:  ## the web page (:5173), proxies /api to :9000
	cd frontend && npm run dev

test-all:  ## fast tests + the slow one that trains models on the synthetic dataset
	uv run pytest -m "slow or not slow"

lint:      ## static checks
	uv run ruff check eda/EDA.py synthetic/synthgen.py tests tools API

format:    ## auto-format EDA.py, synthgen.py, the tests and the notebook generator
	uv run ruff format eda/EDA.py synthetic/synthgen.py tests tools

clean:     ## delete generated reports and caches (never touches nbg_synthetic_data/)
	rm -rf reports .ruff_cache .pytest_cache __pycache__ tests/__pycache__
