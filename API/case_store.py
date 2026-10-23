"""In-memory assessments and persistent, hash-linked audit events with no update/delete API."""

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
        self._cases: dict[str, tuple[str, str]] = {}
        with self.connect() as db:
            db.executescript("""
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
        with self.lock:
            self._cases[case["job_id"]] = (owner, canonical(case))

    def get(self, case_id: str) -> dict | None:
        with self.lock:
            row = self._cases.get(case_id)
        return {"owner": row[0], **json.loads(row[1])} if row else None

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
