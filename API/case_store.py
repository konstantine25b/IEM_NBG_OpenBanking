"""Local case persistence. Audit events have no update/delete API and form a verifiable hash chain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class CaseStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY, owner TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS case_events ON events(case_id, id);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, case: dict, owner: str):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO cases VALUES (?, ?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET "
                "updated_at=excluded.updated_at, payload=excluded.payload",
                (case["job_id"], owner, timestamp(), canonical(case)),
            )

    def get(self, case_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT owner, payload FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return {"owner": row[0], **json.loads(row[1])} if row else None

    def list(self, owner: str | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT owner, updated_at, payload FROM cases "
                + ("WHERE owner=? " if owner else "")
                + "ORDER BY updated_at DESC LIMIT 50",
                (owner,) if owner else (),
            ).fetchall()
        return [
            {
                "case_id": c["job_id"],
                "owner": who,
                "updated_at": when,
                "status": c["status"],
                "subject": c.get("subject"),
                "scenario": c.get("scenario", "none"),
                "review": c.get("review"),
            }
            for who, when, raw in rows
            for c in [json.loads(raw)]
        ]

    def append(self, case_id: str, kind: str, actor: str, detail: dict | None = None) -> dict:
        with self.lock, self.connect() as db:
            last = db.execute(
                "SELECT event_hash FROM events WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)
            ).fetchone()
            previous = last[0] if last else "0" * 64
            event = {"case_id": case_id, "at": timestamp(), "kind": kind, "actor": actor, "detail": detail or {}}
            raw = canonical(event)
            digest = hashlib.sha256((previous + raw).encode()).hexdigest()
            db.execute(
                "INSERT INTO events(case_id,payload,previous_hash,event_hash) VALUES (?,?,?,?)",
                (case_id, raw, previous, digest),
            )
        return {**event, "previous_hash": previous, "event_hash": digest}

    def audit(self, case_id: str) -> dict:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload,previous_hash,event_hash FROM events WHERE case_id=? ORDER BY id", (case_id,)
            ).fetchall()
        previous, valid, events = "0" * 64, True, []
        for raw, expected, digest in rows:
            valid &= expected == previous and hashlib.sha256((expected + raw).encode()).hexdigest() == digest
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                event = None
            if not isinstance(event, dict) or not all(k in event for k in ("case_id", "at", "kind", "actor", "detail")):
                valid = False
                event = {
                    "case_id": case_id,
                    "at": None,
                    "kind": "Malformed audit event",
                    "actor": "unknown",
                    "detail": {},
                }
            events.append({**event, "previous_hash": expected, "event_hash": digest})
            previous = digest
        case = self.get(case_id)
        ready = next((e for e in reversed(events) if e["kind"] == "Case ready for human review"), None)
        reviews = [e for e in events if e["kind"] == "Officer review recorded"]
        result_matches = None
        if case and ready and valid:
            result_matches = hashlib.sha256(canonical(case["result"]).encode()).hexdigest() == ready["detail"][
                "detail"
            ].get("result_sha256")
            if reviews:
                result_matches &= case.get("review") == reviews[-1]["detail"]
        if not valid:
            result_matches = False
        return {
            "chain_valid": bool(valid),
            "case_matches_audit": result_matches,
            "events": events,
            "limitation": "Append-only through this application. Hashes detect edits; this is not an external immutable archive.",
        }
