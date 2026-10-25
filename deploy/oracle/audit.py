#!/usr/bin/env python3
"""Privileged, fixed-directory audit backup/verification helper for this demo."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path("/srv/income-check/runtime")
BACKUPS = Path("/srv/income-check/deployment/backups")


def connect():
    return sqlite3.connect(f"file:{RUNTIME / 'audit.sqlite3'}?mode=ro", uri=True)


def snapshot(cutoff=None):
    with connect() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        rows = db.execute("SELECT id,case_id,payload,previous_hash,event_hash FROM events ORDER BY id").fetchall()
    previous = {}
    for _, case, raw, expected, digest in rows:
        if expected != previous.get(case, "0" * 64) or hashlib.sha256((expected + raw).encode()).hexdigest() != digest:
            raise RuntimeError("Audit hash chain failed")
        previous[case] = digest
    preserved = [r for r in rows if cutoff is None or r[0] <= cutoff]
    return {"rows": len(preserved), "last_id": preserved[-1][0] if preserved else 0,
            "sha256": hashlib.sha256(json.dumps(preserved, separators=(",", ":")).encode()).hexdigest(),
            "sqlite_integrity": "ok", "all_chains_valid": True}


def main():
    action = sys.argv[1]
    if action == "snapshot":
        result = snapshot(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif action == "backup":
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = datetime.now(UTC).strftime("audit-%Y%m%dT%H%M%S%fZ.sqlite3")
        destination = BACKUPS / name
        with connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        os.chmod(destination, 0o600)
        # The deploy service owns its private state directory and can retain its own backups.
        owner = BACKUPS.parent.stat()
        os.chown(destination, owner.st_uid, owner.st_gid)
        result = {"name": name}
    elif action == "restore":
        name = sys.argv[2]
        if not re.fullmatch(r"audit-[0-9TZ]+\.sqlite3", name):
            raise ValueError("Unexpected audit backup name")
        source = BACKUPS / name
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
        quarantine = BACKUPS / datetime.now(UTC).strftime("failed-%Y%m%dT%H%M%S%fZ")
        quarantine.mkdir(mode=0o700)
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = RUNTIME / f"audit.sqlite3{suffix}"
            if path.exists():
                shutil.move(path, quarantine / path.name)
        shutil.copyfile(source, RUNTIME / "audit.sqlite3")
        os.chown(RUNTIME / "audit.sqlite3", 10001, 10001)
        os.chmod(RUNTIME / "audit.sqlite3", 0o600)
        result = {"restored": name, "failed_files_preserved": str(quarantine)}
    else:
        raise ValueError("Unknown audit operation")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
