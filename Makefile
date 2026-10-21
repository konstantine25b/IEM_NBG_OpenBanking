# Shortcuts for the EDA project.  Everything runs through uv, so the right virtualenv is used automatically.
.PHONY: help setup eda eda-fast eda-core eda-features notebook notebook-check synth synth-check synth-bank test test-all lint format clean

help:      ## list the commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'

setup:     ## create / update .venv from uv.lock
	uv sync

eda:       ## full EDA  -> reports/eda_report.html (a few minutes)
	uv run python EDA.py

eda-fast:  ## 20k-row smoke run (about two minutes with the mock part)
	uv run python EDA.py --fast

eda-core:  ## without the mock / synthetic bank data: sections 1-10 only (about 3.5 minutes)
	uv run python EDA.py --no-mock

eda-features:  ## full run + reports/mock_customer_features.csv (one row per mock customer, synthetic)
	uv run python EDA.py --export-mock-features

notebook:  ## (re)generate EDA.ipynb from EDA.py (keeps your edits safe: it refuses to overwrite changed cells)
	uv run python tools/make_notebook.py

notebook-check:  ## fail when EDA.ipynb is out of date with EDA.py
	uv run python tools/make_notebook.py --check

synth:     ## 100,000 new synthetic loans -> synthetic/loans (schema of Data/, gates in FIDELITY_REPORT.md; ~45 s)
	uv run python synthgen.py

synth-check:  ## fidelity + privacy + train-synthetic/test-real on synthetic/loans
	uv run python synthgen.py check --full

synth-bank:  ## bank API data for 2,000 synthetic loans through the unchanged openbanking_mock chain -> synthetic/bank_dataset
	uv run python synthgen.py bank --customers 2000

test:      ## fast tests: statistics helpers + the whole script on a synthetic dataset
	uv run pytest

test-all:  ## fast tests + the slow one that trains models on the synthetic dataset
	uv run pytest -m "slow or not slow"

lint:      ## static checks
	uv run ruff check EDA.py synthgen.py tests tools

format:    ## auto-format EDA.py, synthgen.py, the tests and the notebook generator
	uv run ruff format EDA.py synthgen.py tests tools

clean:     ## delete generated reports and caches (never touches Data/)
	rm -rf reports .ruff_cache .pytest_cache __pycache__ tests/__pycache__
