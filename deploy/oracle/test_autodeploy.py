"""Deployment safety regressions using an isolated container/state model; no model training."""
import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import audit
import autodeploy as module


class SimulatedDocker(module.Deployer):
    def __init__(self, *, fail_smoke=False, damage_audit=False):
        super().__init__()
        self.containers = {module.NAME: {
            "Id": "old-container", "Config": {"Image": "previous-image", "Labels": {}},
            "State": {"Running": True, "ExitCode": 0}, "RestartCount": 2,
            "HostConfig": {"PortBindings": {"10000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "10000"}]}},
        }}
        self.events = []
        self.fail_smoke = fail_smoke
        self.damage_audit = damage_audit
        self.damaged = False
        self.restored = False
        self.audit_rows = {"rows": 10, "last_id": 10, "sha256": "original", "all_chains_valid": True}

    def container(self, name=module.NAME):
        return copy.deepcopy(self.containers.get(name))

    def run(self, *args, **kwargs):
        self.events.append(args)
        if args[:2] == ("docker", "stop"):
            self.containers[args[-1]]["State"]["Running"] = False
        elif args[:2] == ("docker", "rename"):
            self.containers[args[3]] = self.containers.pop(args[2])
        elif args[:2] == ("docker", "start"):
            self.containers[args[2]]["State"]["Running"] = True
        elif args[:2] == ("docker", "rm"):
            self.containers.pop(args[-1])
        else:
            raise AssertionError(args)
        return ""

    def create(self, image, name, port, *, persistent, revision):
        self.containers[name] = {
            "Id": "new-container", "Config": {"Image": image, "Labels": {module.OWNER: "true"}},
            "State": {"Running": True, "ExitCode": 0}, "RestartCount": 0,
        }
        if persistent and self.damage_audit:
            self.damaged = True

    def wait_ready(self, *args, **kwargs):
        pass

    def smoke(self, *args, **kwargs):
        if self.fail_smoke:
            raise RuntimeError("Realistic failed consent/inference check")

    def audit(self, action="snapshot", *args):
        if action == "backup":
            return {"name": "audit-20261025T000000Z.sqlite3"}
        if action == "restore":
            self.restored = True
            self.damaged = False
            return {"restored": True}
        return self.audit_rows | ({"sha256": "mutated"} if self.damaged else {})


class DeploymentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name)
        self.patch = patch.object(module, "STATE", self.state)
        self.patch.start()
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.patch.stop()
        self.directory.cleanup()

    def test_failed_candidate_never_stops_live_application(self):
        worker = SimulatedDocker(fail_smoke=True)
        with self.assertRaises(RuntimeError):
            worker.verify_candidate("candidate-image", "a" * 40)
        self.assertEqual(worker.container()["Id"], "old-container")
        self.assertTrue(worker.container()["State"]["Running"])
        self.assertNotIn(module.CANDIDATE, worker.containers)
        self.assertFalse(any(event[:2] == ("docker", "stop") for event in worker.events))

    def test_failed_post_cutover_check_restores_previous_container(self):
        worker = SimulatedDocker(fail_smoke=True)
        with self.assertRaises(RuntimeError):
            worker.activate("candidate-image", "a" * 40)
        self.assertEqual(worker.container()["Id"], "old-container")
        self.assertTrue(worker.container()["State"]["Running"])
        self.assertFalse((self.state / "transaction.json").exists())
        self.assertFalse(worker.restored, "Unchanged audit must not be replaced")

    def test_mutated_audit_triggers_backup_restore_before_old_start(self):
        worker = SimulatedDocker(damage_audit=True)
        with self.assertRaises(RuntimeError):
            worker.activate("candidate-image", "a" * 40)
        self.assertTrue(worker.restored)
        self.assertEqual(worker.container()["Id"], "old-container")
        self.assertFalse(worker.damaged)

    def test_verified_deployment_removes_only_previous_container(self):
        worker = SimulatedDocker()
        worker.containers["unrelated"] = {"Id": "unrelated", "Config": {"Labels": {}}}
        worker.activate("candidate-image", "a" * 40)
        self.assertEqual(worker.container()["Config"]["Image"], "candidate-image")
        self.assertIn("unrelated", worker.containers)
        self.assertNotIn("income-check-rollback-old-containe", worker.containers)
        success = json.loads((self.state / "last-success.json").read_text())
        self.assertEqual(success["previous_image"], "previous-image")
        self.assertEqual(success["audit_preserved"], worker.audit_rows)

    def test_interrupted_cutover_is_recovered_on_next_run(self):
        worker = SimulatedDocker()
        backup = "income-check-rollback-old"
        worker.run("docker", "stop", "--timeout", "30", module.NAME)
        worker.run("docker", "rename", module.NAME, backup)
        worker.create("candidate-image", module.NAME, 10000, persistent=True, revision="a" * 40)
        module.atomic_json(self.state / "transaction.json", {
            "old_id": "old-container", "old_image": "previous-image", "backup": backup,
            "commit": "a" * 40, "image": "candidate-image", "audit": worker.audit_rows,
            "audit_backup": "audit-20261025T000000Z.sqlite3",
        })
        worker.recover()
        self.assertEqual(worker.container()["Id"], "old-container")
        self.assertTrue(worker.container()["State"]["Running"])

    def test_cleanup_refuses_unrelated_container_with_reserved_name(self):
        worker = SimulatedDocker()
        worker.containers[module.CANDIDATE] = {"Id": "someone-else", "Config": {"Labels": {}}}
        with self.assertRaisesRegex(RuntimeError, "unrelated"):
            worker.remove_owned(module.CANDIDATE)
        self.assertIn(module.CANDIDATE, worker.containers)

    def poll_worker(self, running):
        worker = SimulatedDocker()
        worker.containers[module.NAME]["State"]["Running"] = running
        worker.containers[module.NAME]["Config"]["Labels"] = {"org.opencontainers.image.revision": "a" * 40}

        def command(*args, **kwargs):
            worker.events.append(args)
            if args == ("uname", "-m"):
                return "aarch64"
            if args == ("git", "remote", "get-url", "origin"):
                return module.REMOTE
            if args[:2] == ("git", "fetch"):
                return ""
            if args == ("git", "rev-parse", "origin/main"):
                return "a" * 40
            raise AssertionError(f"Unexpected action during a no-change/stopped poll: {args}")

        worker.run = command
        return worker

    def test_unchanged_poll_never_rebuilds_or_restarts(self):
        worker = self.poll_worker(running=True)
        worker.deploy()
        self.assertEqual(worker.container()["Id"], "old-container")
        self.assertFalse(any(event[0] == "docker" for event in worker.events))

    def test_manually_stopped_application_is_not_started_by_poller(self):
        worker = self.poll_worker(running=False)
        worker.deploy(force=True)
        self.assertFalse(worker.container()["State"]["Running"])
        self.assertFalse(any(event[0] == "docker" for event in worker.events))


class AuditPrefixTests(unittest.TestCase):
    def test_empty_audit_prefix_allows_new_verified_events(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(audit, "RUNTIME", Path(directory)):
            path = Path(directory) / "audit.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY,case_id,payload,previous_hash,event_hash)")
            before = audit.snapshot()
            with sqlite3.connect(path) as db:
                raw = json.dumps({"kind": "first new event"})
                digest = hashlib.sha256(("0" * 64 + raw).encode()).hexdigest()
                db.execute("INSERT INTO events VALUES (1,'case',?,?,?)", (raw, "0" * 64, digest))
            self.assertEqual(audit.snapshot(0), before)
            self.assertEqual(audit.snapshot()["rows"], 1)

    def test_new_rows_do_not_invalidate_preserved_audit_prefix(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(audit, "RUNTIME", Path(directory)):
            path = Path(directory) / "audit.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY,case_id,payload,previous_hash,event_hash)")
                raw = json.dumps({"kind": "existing"})
                first = hashlib.sha256(("0" * 64 + raw).encode()).hexdigest()
                db.execute("INSERT INTO events VALUES (1,'case',?,?,?)", (raw, "0" * 64, first))
            before = audit.snapshot()
            with sqlite3.connect(path) as db:
                raw = json.dumps({"kind": "new legitimate event"})
                second = hashlib.sha256((first + raw).encode()).hexdigest()
                db.execute("INSERT INTO events VALUES (2,'case',?,?,?)", (raw, first, second))
            self.assertEqual(audit.snapshot(before["last_id"]), before)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE events SET payload='tampered' WHERE id=1")
            with self.assertRaisesRegex(RuntimeError, "hash chain"):
                audit.snapshot(before["last_id"])


if __name__ == "__main__":
    unittest.main()
