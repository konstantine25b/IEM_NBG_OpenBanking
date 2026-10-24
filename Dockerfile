# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM python:3.12-slim-bookworm AS python-deps
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    FORWARDED_ALLOW_IPS="*" \
    PORT=10000
WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY API ./API
COPY income/*.py ./income/
COPY income/models ./income/models
COPY eda/EDA.py ./eda/EDA.py
COPY openbanking_mock/obmock ./openbanking_mock/obmock
COPY openbanking_mock/specs ./openbanking_mock/specs
COPY openbanking_mock/fixtures ./openbanking_mock/fixtures
COPY openbanking_mock/output/dataset/MANIFEST.json ./openbanking_mock/output/dataset/MANIFEST.json
COPY openbanking_mock/output/dataset/truth/customers ./openbanking_mock/output/dataset/truth/customers
COPY nbg_synthetic_data ./nbg_synthetic_data
COPY tools/start_production.py ./tools/start_production.py
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/.runtime && chown app:app /app/.runtime
USER app
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/health',timeout=4).close()"
CMD ["python", "tools/start_production.py"]

# Optional regression target; development dependencies never enter the default image.
FROM python-deps AS test-deps
RUN uv sync --locked --no-install-project --no-editable
FROM runtime AS test
COPY --from=test-deps /opt/venv /opt/venv
COPY pyproject.toml ./
COPY tests ./tests
CMD ["python", "-m", "pytest", "tests/test_demo_workspace.py", "tests/test_production.py", "-p", "no:cacheprovider"]

FROM runtime AS production
