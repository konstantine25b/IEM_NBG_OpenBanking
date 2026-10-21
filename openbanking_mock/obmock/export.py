"""Replay of what an AIS client (TPP) does against a bank's API, recorded call by call.

`capture_customer` is the only road from the simulated world to the dataset: the pipeline crawls every
customer through the bank dialects exactly as a TPP would (accounts -> balances -> every transaction
page -> cards -> loans -> savings) and `harvest.py` flattens the responses. Nothing the API does not
return can reach the API tables. (The previous dump of the internal ledger was removed on purpose.)
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from obmock.engine.gateway import Gateway, Request


# ----------------------------------------------------------------------------- API captures
class _Recorder:
    def __init__(self, gw: Gateway, bank: str, cid: str):
        self.gw, self.bank, self.cid = gw, bank, cid
        self.calls: list[dict[str, Any]] = []
        self.d = gw.dialects[bank]

    def get(self, path: str, query: dict[str, str] | None = None) -> tuple[int, Any]:
        q = dict(query or {})
        path = "/" + path.lstrip("/")
        headers = {"x-mock-customer": self.cid, "x-request-id": "export",
                   "accept": "application/json"}
        resp = self.gw.handle(self.bank, Request("GET", path, q, headers, None,
                                                 f"http://localhost:8000/{self.bank}"))
        self.calls.append({"method": "GET", "path": path, "query": q, "status": resp.status,
                           "body": resp.body})
        return resp.status, resp.body

    def crawl(self, path: str, query: dict[str, str]) -> None:
        """GET `path` and follow `_links.next` until exhausted."""
        status, body = self.get(path, query)
        for _ in range(500):
            nxt = self._next(body) if status == 200 else None
            if not nxt:
                return
            parts = urlsplit(nxt)
            status, body = self.get(parts.path, dict(parse_qsl(parts.query)))

    @staticmethod
    def _next(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        for holder in (body, *[v for v in body.values() if isinstance(v, dict)]):
            href = (holder.get("_links") or {}).get("next")
            if isinstance(href, dict) and href.get("href"):
                return href["href"]
        return None


_LIST_OPS = ("accounts_list", "cards_list", "cards2_list", "loans_list", "savings_list")


def capture_customer(gw: Gateway, bank: str, cid: str, date_from: str) -> dict[str, Any]:
    """Replay what an AIS client would do for one customer at one bank."""
    rec = _Recorder(gw, bank, cid)
    d = rec.d
    lists = {e[2]: e[1] for e in d.route_table() if e[0] == "GET" and e[2] in _LIST_OPS}
    for op, tmpl in lists.items():
        q = {"withBalance": "true"} if op == "accounts_list" else {}
        status, body = rec.get(tmpl, q)
        if status == 400 and q:  # e.g. Paysera rejects withBalance
            status, body = rec.get(tmpl)
        if status != 200 or not isinstance(body, dict):
            continue
        items = next((v for v in body.values() if isinstance(v, list)), [])
        for it in items:
            if not isinstance(it, dict):
                continue
            links = it.get("_links") or it.get("links") or {}
            if it.get("resourceId"):
                rec.get(f"{tmpl}/{it['resourceId']}")
            for sub in ("balances", "transactions"):
                href = (links.get(sub) or {}).get("href")
                if not href:
                    continue
                path = "/" + urlsplit(href).path.lstrip("/")
                if sub == "transactions":
                    rec.crawl(path, {"bookingStatus": "both", "dateFrom": date_from})
                else:
                    rec.get(path)
    return {"bank": bank, "customer": cid, "dialect": d.key, "calls": rec.calls}
