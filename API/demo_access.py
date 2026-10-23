"""Small local-demo session boundary; replace these accounts with bank SSO before deployment."""

from __future__ import annotations

import os
import secrets
import time

from fastapi import HTTPException, Request


class DemoAccess:
    def __init__(self):
        self.sessions = {}
        self.passwords = {
            "officer": os.getenv("DEMO_OFFICER_PASSWORD", "demo-review-only"),
            "auditor": os.getenv("DEMO_AUDITOR_PASSWORD", "demo-audit-only"),
        }

    def login(self, username: str, password: str) -> tuple[str, dict]:
        if username not in self.passwords or not secrets.compare_digest(password, self.passwords[username]):
            raise HTTPException(401, "Invalid demo account or password")
        token = secrets.token_urlsafe(32)
        principal = {"username": username, "role": username}
        self.sessions[token] = {**principal, "expires": time.time() + 8 * 3600}
        return token, principal

    def require(self, request: Request, write: bool = False) -> dict:
        session = self.sessions.get(request.cookies.get("income_session", ""))
        if not session or session["expires"] <= time.time():
            raise HTTPException(401, "Sign in to the local demo")
        if write:
            if session["role"] != "officer":
                raise HTTPException(403, "Only an officer can create or review a case")
            origin = request.headers.get("origin")
            allowed = {
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:9000",
                "http://localhost:9000",
            }
            if origin and origin not in allowed:
                raise HTTPException(403, "Cross-origin write rejected")
        return {k: session[k] for k in ("username", "role")}
