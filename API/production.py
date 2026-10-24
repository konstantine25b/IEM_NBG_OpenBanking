"""Production HTTP entrypoint: existing income API plus the compiled frontend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from API.service import create_app as create_income_app

ROOT = Path(__file__).resolve().parents[1]


class APIPrefix:
    """Dispatch /api/* to the unchanged routes, preserving the ASGI request body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            scope = dict(scope)
            scope["path"] = scope["path"][4:]
            scope["raw_path"] = scope.get("raw_path", b"")[4:]
        await self.app(scope, receive, send)


def serve_frontend(app: FastAPI, directory: Path) -> FastAPI:
    if not (directory / "index.html").is_file():
        raise RuntimeError(f"Compiled frontend missing at {directory}; build the Docker image first")
    app.add_middleware(APIPrefix)
    # Register last, so /health, /v1/*, /docs, /redoc and /openapi.json retain priority.
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
    return app


def create_app() -> FastAPI:
    app = create_income_app()
    svc = app.state.service

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            svc.pool.shutdown(wait=True, cancel_futures=True)
            svc.banks.close()
            svc.registries.close()

    app.router.lifespan_context = lifespan
    return serve_frontend(app, ROOT / "frontend" / "dist")
