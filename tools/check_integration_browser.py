"""Exercise the combined UI on private ports without restarting the normal demo.

Run after npm ci and a production build. Requires Playwright and Edge on Windows,
or Playwright Chromium on other systems. Logs stay in .runtime/browser-regression/.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORTS = (18000, 18001, 19000, 15173)


def main():
    for port in PORTS:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Test port {port} is occupied; no existing service was stopped.")
    local_node = ROOT / "frontend/node_modules/node/bin/node.exe"
    node = str(local_node) if local_node.exists() else shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required.")
    logs = ROOT / ".runtime/browser-regression"
    logs.mkdir(parents=True, exist_ok=True)
    commands = [
        ("banks", [sys.executable, "API/mock_banks.py", "--port", "18000"], ROOT),
        ("registries", [sys.executable, "API/mock_registries.py", "--port", "18001"], ROOT),
        ("income", [sys.executable, "API/service.py", "--port", "19000", "--banks-url",
                    "http://127.0.0.1:18000", "--registries-url", "http://127.0.0.1:18001"], ROOT),
        ("frontend", [node, "--input-type=module", "--eval",
                      "import {createServer} from 'vite'; const server=await createServer({"
                      "cacheDir:'.runtime/browser-vite',server:{host:'127.0.0.1',port:15173,strictPort:true}});"
                      "await server.listen();"], ROOT / "frontend"),
    ]
    processes, files = [], []
    try:
        for name, command, cwd in commands:
            log = (logs / f"{name}.log").open("w", encoding="utf-8")
            files.append(log)
            processes.append(subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(f"A regression service exited. Inspect {logs}.")
            try:
                for port, path in zip(PORTS, ("/_mock/clock", "/openapi.json", "/health", "/"), strict=True):
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1).close()
                break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError("Regression services did not become ready.")
        check_browser()
    finally:
        cleanup_errors = []
        for process in processes:
            if process.poll() is None:
                if os.name == "nt":
                    stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                    if stopped.returncode:
                        cleanup_errors.append(f"PID {process.pid}: {stopped.stderr.decode(errors='replace').strip()}")
                else:
                    process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup_errors.append(f"PID {process.pid} did not stop")
        for log in files:
            log.close()
        if cleanup_errors:
            raise RuntimeError("Regression cleanup failed: " + "; ".join(cleanup_errors))


def check_browser():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**({"channel": "msedge"} if os.name == "nt" else {}), headless=True)

        job_ids = []  # ids of the assessments the page started, read from the API replies

        def page_for(*, failure="none", mobile=False):
            page = browser.new_page(viewport={"width": 390 if mobile else 1440, "height": 1000}, reduced_motion="reduce")
            page.on("pageerror", lambda error: errors.append(str(error)))

            def forward(route):
                # Serve this worktree's UI on a private port. Forward only this test page's
                # API traffic to its isolated service using the normal allowed local origin.
                path = urlsplit(route.request.url).path.removeprefix("/api")
                headers = {**route.request.headers, "origin": "http://127.0.0.1:5173"}
                options = {"url": f"http://127.0.0.1:19000{path}", "headers": headers}
                if path == "/v1/income-estimates" and route.request.method == "POST":
                    options["post_data"] = json.dumps({**route.request.post_data_json, "demo_failure": failure})
                response = route.fetch(**options)
                if path == "/v1/income-estimates" and route.request.method == "POST":
                    job_ids.append(response.json()["job_id"])
                route.fulfill(response=response)

            page.route("**/api/**", forward)
            return page

        def completed(page):
            expect(page.get_by_role("heading", name="Underlying transaction evidence")).to_be_visible(timeout=30000)
            expect(page.get_by_text("Hash chain and case result verified", exact=True)).to_be_visible(timeout=10000)

        page = page_for()
        page.goto("http://127.0.0.1:15173/", wait_until="networkidle")
        expect(page.locator(".client")).to_have_count(20)
        page.locator(".client").filter(has_text="C50811").click()
        page.get_by_role("button", name="Connect all banks", exact=True).click()
        page.get_by_role("button", name="Estimate income", exact=True).wait_for(timeout=20000)
        expect(page.locator(".stepper .next")).to_contain_text("Collect data")
        expect(page.locator(".profile")).to_contain_text("Confirmed by")
        page.get_by_role("button", name="Estimate income", exact=True).click()
        completed(page)
        expect(page.locator(".hero .big")).to_contain_text("1,878", timeout=10000)
        expect(page.locator(".score-num strong")).to_have_text("81%", timeout=10000)
        evidence = page.get_by_role("heading", name="Underlying transaction evidence").locator("..")
        expect(evidence.get_by_label("Activity filter", exact=True)).to_have_value("all")
        expect(evidence).to_contain_text("1015 records")
        evidence.get_by_label("Activity filter", exact=True).select_option("service")
        expect(evidence).to_contain_text("76 records")
        evidence.get_by_role("button", name="Next", exact=True).click()
        expect(evidence).to_contain_text("Page 2 / 4")
        for source, message in (("rs", "Income from remittances"), ("creditinfo", "6 records")):
            evidence.get_by_label("Evidence source", exact=True).select_option(source)
            expect(evidence).to_contain_text(message)
        page.get_by_role("button", name="Show all factors", exact=True).click()
        expect(page.locator(".drivers .share").first).to_be_visible()
        page.locator(".drivers details summary").first.click()
        expect(page.locator(".features").first).to_be_visible()
        combined = page.get_by_role("heading", name="Processing evidence and audit trail").locator("..")
        times = combined.locator("time").all()
        assert len(times) > 15
        assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", t.get_attribute("datetime")) for t in times)
        case_id = job_ids[-1]
        case = page.request.get(f"http://127.0.0.1:19000/v1/income-estimates/{case_id}").json()
        expected_times = {datetime.fromisoformat(e["at"]).isoformat(timespec="milliseconds").replace("+00:00", "Z") for e in case["trace"]}
        assert expected_times <= {t.get_attribute("datetime") for t in times}
        expect(page.get_by_role("button", name=re.compile("model-risk evaluation"))).to_have_count(0)
        for listing in page.locator(".notice ul").all():
            assert listing.evaluate("el => getComputedStyle(el).listStyleType") == "none"
        for name in ("Saved cases", "Human review of the income estimate", "Measured manual-task comparison"):
            expect(page.get_by_role("heading", name=name, exact=True)).to_have_count(0)
        expect(page.locator("a[download], input[type=password]")).to_have_count(0)
        expect(page.get_by_text("Service receipts by calendar month", exact=True)).to_have_count(0)
        expect(page.get_by_label("Recording scenario")).to_have_count(0)
        print("PASS desktop: both profiles/SHAP and evidence/audit features, 20 clients, exact timestamps, warnings, removed controls", flush=True)
        for failure in ("rs_unavailable", "banks_unavailable"):
            scenario = page_for(failure=failure)
            scenario.goto("http://127.0.0.1:15173/?client=91038618469", wait_until="networkidle")
            completed(scenario)
            if failure == "rs_unavailable":
                expect(scenario.get_by_text(re.compile("No Revenue Service data"))).to_be_visible()
                expect(scenario.locator(".hero .big")).to_be_visible()
            else:
                expect(scenario.get_by_role("heading", name=re.compile("Insufficient evidence"))).to_be_visible()
            assert scenario.locator(".progress li.failed .warning-label").count() > 0
        discovery = page_for()
        discovery.route("**/api/v1/sources", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"Source discovery unavailable"}'))
        discovery.goto("http://127.0.0.1:15173/?client=91038618469", wait_until="networkidle")
        completed(discovery)
        mobile = page_for(mobile=True)
        mobile.goto("http://127.0.0.1:15173/", wait_until="networkidle")
        expect(mobile.locator(".demo-list")).not_to_have_attribute("open", "")
        mobile.locator(".demo-list > summary").click()
        mobile.locator(".client").filter(has_text="C50811").click()
        expect(mobile.locator(".demo-list")).not_to_have_attribute("open", "")
        mobile.get_by_role("button", name="Connect all banks", exact=True).click()
        mobile.get_by_role("button", name="Estimate income", exact=True).click(timeout=20000)
        completed(mobile)
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert not errors, errors
        browser.close()
        print("PASS source failures, safe abstention, mobile collapse/layout; no browser errors", flush=True)


if __name__ == "__main__":
    main()
