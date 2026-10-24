"""HTTP deployment regressions; no generated data or model training."""

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from API.production import serve_frontend


@pytest.fixture
def web(tmp_path):
    (tmp_path / "index.html").write_text("<html>Compiled frontend</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('compiled')", encoding="utf-8")
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/echo", status_code=201)
    async def echo(request: Request):
        return {"body": await request.json(), "query": dict(request.query_params)}

    return TestClient(serve_frontend(app, tmp_path))


def test_compiled_frontend_and_assets(web):
    assert "Compiled frontend" in web.get("/").text
    script = web.get("/assets/app.js")
    assert script.status_code == 200
    assert "compiled" in script.text
    assert web.get("/missing.js").status_code == 404
    assert web.get("/api/missing").status_code == 404
    assert web.get("/.env").status_code == 404


@pytest.mark.parametrize("prefix", ["", "/api"])
def test_prefix_preserves_post_body_query_and_status(web, prefix):
    response = web.post(prefix + "/v1/echo?source=bank", json={"personal_id": "synthetic", "nested": [1, 2]})
    assert response.status_code == 201
    assert response.json() == {"body": {"personal_id": "synthetic", "nested": [1, 2]}, "query": {"source": "bank"}}
    assert web.get(prefix + "/health").json() == {"status": "ok"}
    assert web.get(prefix + "/docs").status_code == 200
    assert "/v1/echo" in web.get(prefix + "/openapi.json").json()["paths"]


def test_missing_build_fails_explicitly(tmp_path):
    with pytest.raises(RuntimeError, match="Compiled frontend missing"):
        serve_frontend(FastAPI(), Path(tmp_path))
