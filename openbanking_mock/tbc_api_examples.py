#!/usr/bin/env python3
"""Export TBC examples to JSON, or run configured read-only sandbox GETs.

Default: no network and no third-party packages. Use --help for commands.
Live signing follows TBCBank/TBC.OpenBanking.Jws HttpSigner/HttpRequestData.
Live interoperability has NOT been verified against an authorized TBC sandbox.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit
import uuid

ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = ROOT / "output/tbc_openbanking_examples/catalog.json"
SANDBOX_HOSTS = {"test-openbanking.tbcbank.ge", "test-api.tbcbank.ge"}
FAMILIES = {
    "accounts": ("/v1/accounts", "accounts", "ais"),
    "cards": ("/v1/card-accounts", "cardAccounts", "ais"),
    "loans": ("/v2/loans", "accounts", "extended"),
    "savings": ("/v2/savings", "accounts", "extended"),
}


class SetupError(Exception):
    """A local configuration or selection problem; not a bank response."""


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"Cannot load JSON file {path}: {exc}") from exc


def resolve(spec, value):
    seen = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if ref in seen or not ref.startswith("#/"):
            raise SetupError(f"Unsupported schema reference: {ref}")
        seen.add(ref)
        value = spec
        for part in ref[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def select_operations(catalog, scope, only):
    ops = [op for op in catalog["operations"]
           if scope == "all" or op["scope"] == "ML data read"]
    if only:
        keys = {key.strip() for key in only.split(",")}
        selected = []
        matched = set()
        for op in ops:
            aliases = {op["id"], op["reference_slug"], op["id"].split("_", 1)[0],
                       str(int(op["id"].split("_", 1)[0]))}
            if aliases & keys:
                selected.append(op)
                matched.update(aliases & keys)
        if keys - matched:
            raise SetupError("Unknown/out-of-scope operation: " + ", ".join(sorted(keys - matched)))
        return selected
    return ops


def published_result(op, status=None, example_index=0):
    """Use embedded response examples, never request-body examples."""
    responses = [r for r in op["responses"]
                 if r["status"] == status] if status else [
                     r for r in op["responses"] if r["status"].startswith("2")]
    examples = [(r, media, ex) for r in responses for media in r["media_types"]
                for ex in media["published_examples"] if "payload" in ex]
    base = {"operation_id": op["id"], "method": op["method"], "path": op["path"],
            "source_url": op["source_url"], "network_request_made": False}
    if examples:
        if example_index >= len(examples):
            return {**base, "classification": "example_not_found",
                    "message": f"Only {len(examples)} example(s) for this status selection."}
        response, media, example = examples[example_index]
        return {**base, "classification": "bank_published_example",
                "http_status_documented": response["status"],
                "content_type_documented": media["content_type"],
                "example_name": example["name"], "body": example["payload"]}
    if (not status or status == "200") and "schema_illustration" in op:
        return {**base, "classification": "synthetic_schema_illustration",
                "message": "Constructed values; NOT a bank-published or live response.",
                "body": op["schema_illustration"]["payload"]}
    if responses and all(not r["media_types"] for r in responses):
        return {**base, "classification": "documented_no_body",
                "documented_statuses": [r["status"] for r in responses], "body": None}
    if not status:
        redirects = [r for r in op["responses"] if r["status"].startswith("3")]
        if redirects:
            return {**base, "classification": "documented_redirect",
                    "documented_statuses": [r["status"] for r in redirects],
                    "message": "Redirect is documented; no JSON success body is invented."}
    schemas = [{"status": r["status"], "content_type": media["content_type"],
                "schema": media["schema"], "fields": media["schema_fields"]}
               for r in responses for media in r["media_types"]]
    return {**base, "classification": "schema_only",
            "message": "No complete published response example for this selection.",
            "response_schemas": schemas, "schema_file": op["schema_file"]}


def family_for(op):
    for family, (prefix, _, _) in FAMILIES.items():
        if op["path"] == prefix or op["path"].startswith(prefix + "/"):
            return family
    # Preserve the published TBC typo. Never silently repair it.
    if op["path"].startswith("/v2/loans{resourceId}"):
        return "loans"
    raise SetupError("Live mode supports only the 14 ML data-read operations.")


def sandbox_base(profile, op):
    base = str(profile.get("base_url", "")).rstrip("/")
    parts = urlsplit(base)
    if (parts.scheme != "https" or parts.hostname not in SANDBOX_HOSTS
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.port not in (None, 443)):
        raise SetupError("base_url must be a documented HTTPS TBC sandbox URL.")
    documented = {s["url"].rstrip("/") for s in op["servers"]
                  if urlsplit(s["url"]).hostname in SANDBOX_HOSTS}
    if base not in documented:
        raise SetupError("base_url is not listed for this operation: " + ", ".join(sorted(documented)))
    return base


def env_value(name):
    if not name or not isinstance(name, str):
        raise SetupError("An environment-variable name is missing from the configuration.")
    value = os.environ.get(name, "")
    if not value:
        raise SetupError(f"Set environment variable {name} before live mode.")
    if "\r" in value or "\n" in value:
        raise SetupError(f"Environment variable {name} contains invalid line breaks.")
    return value


def file_setting(config, name, config_dir, required=True):
    value = config.get(name)
    if not value:
        if not required:
            return None
        raise SetupError(f"Configure {name} in the sandbox JSON configuration.")
    path = Path(value).expanduser()
    path = path if path.is_absolute() else config_dir / path
    if not path.is_file():
        raise SetupError(f"Configured {name} file does not exist: {path}")
    return path


def b64url(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class RequestSigner:
    """RSA detached JWS using the header composition in TBC's official library.

    Source: https://github.com/TBCBank/TBC.OpenBanking.Jws
    HttpRequestData.cs, HttpSigner.cs, ProtectedHeader.cs.
    This is locally tested request signing, not bank-certified interoperability.
    """

    def __init__(self, config, config_dir):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError as exc:
            raise SetupError("Live mode needs: python3 -m pip install -r requirements-tbc.txt") from exc
        self.algorithm = config.get("signing_algorithm", "RS256")
        if self.algorithm not in {"RS256", "PS256"}:
            raise SetupError("This runner implements RSA RS256 and PS256 signing only.")
        chain_file = file_setting(config, "signing_certificate_chain", config_dir)
        key_file = file_setting(config, "signing_private_key", config_dir)
        password_env = config.get("signing_key_password_env")
        password = env_value(password_env).encode() if password_env else None
        try:
            self.key = serialization.load_pem_private_key(key_file.read_bytes(), password)
            blocks = re.findall(b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                                chain_file.read_bytes(), re.S)
            certificates = [x509.load_pem_x509_certificate(block) for block in blocks]
        except (ValueError, TypeError) as exc:
            raise SetupError("Cannot load signing PEM files; check key format and password.") from exc
        if not certificates or not isinstance(self.key, rsa.RSAPrivateKey):
            raise SetupError("Supply an RSA signing key and its PEM certificate chain, leaf first.")
        public_format = serialization.PublicFormat.SubjectPublicKeyInfo
        encoding = serialization.Encoding.DER
        if self.key.public_key().public_bytes(encoding, public_format) != certificates[0].public_key().public_bytes(encoding, public_format):
            raise SetupError("Signing key does not match the first certificate in the chain.")
        self.x5c = [base64.b64encode(c.public_bytes(encoding)).decode("ascii") for c in certificates]

    def sign(self, prepared):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if prepared.method != "GET" or prepared.body:
            raise SetupError("This signer is intentionally limited to empty-body GET requests.")
        url = urlsplit(prepared.url)
        target = (url.path or "/") + ("?" + url.query if url.query else "")
        prepared.headers["Host"] = url.netloc
        prepared.headers["Digest"] = "SHA-256=" + base64.b64encode(hashlib.sha256(b"").digest()).decode()
        headers = {name.lower(): value for name, value in prepared.headers.items()}
        names = ["(request-target)", "host", "x-request-id"]
        names += [name for name in ("content-type", "content-length") if name in headers]
        names += ["digest"] + [name for name in headers if name.startswith("psu-")]
        protected = {
            "b64": False, "x5c": self.x5c, "crit": ["sigT", "sigD", "b64"],
            "sigT": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sigD": {"pars": names, "mId": "http://uri.etsi.org/19182/HttpHeaders"},
            "alg": self.algorithm,
        }
        encoded = b64url(json.dumps(protected, separators=(",", ":")).encode())
        values = {**headers, "(request-target)": "get " + target}
        payload = "\n".join(name + ": " + values[name].strip() for name in names)
        # TBC's implementation signs ASCII header lines; reject silent character replacement.
        to_sign = (encoded + "." + payload).encode("ascii")
        pad = (padding.PKCS1v15() if self.algorithm == "RS256" else
               padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32))
        prepared.headers["X-JWS-Signature"] = encoded + ".." + b64url(self.key.sign(to_sign, pad, hashes.SHA256()))


class SandboxReader:
    def __init__(self, catalog, config, config_dir, catalog_root, session=None, signer=None):
        try:
            import requests
        except ImportError as exc:
            raise SetupError("Live mode needs: python3 -m pip install -r requirements-tbc.txt") from exc
        self.requests = requests
        self.catalog, self.config = catalog, config
        self.config_dir, self.catalog_root = config_dir, catalog_root
        self.session = session or requests.Session()
        self.signer = signer
        self.ids = dict(config.get("resource_ids", {}))
        self.roots = {}
        self.records = []
        self.timeout = float(config.get("timeout_seconds", 20))
        if not 0 < self.timeout <= 120:
            raise SetupError("timeout_seconds must be between 0 and 120.")

    def parameters(self, op):
        path = self.catalog_root / op["schema_file"]
        spec = read_json(path)
        operation = spec["paths"][op["path"]]["get"]
        return [resolve(spec, p) for p in operation.get("parameters", [])]

    def _transport(self):
        if self.signer is None:
            self.signer = RequestSigner(self.config, self.config_dir)
        cert = file_setting(self.config, "mtls_certificate", self.config_dir)
        key = file_setting(self.config, "mtls_private_key", self.config_dir)
        self.session.cert = (str(cert), str(key))
        ca = file_setting(self.config, "ca_bundle", self.config_dir, required=False)
        self.session.verify = str(ca) if ca else True

    def run(self, op, discovery=False):
        family = family_for(op)
        root_path, root_key, profile_key = FAMILIES[family]
        base_record = {"operation_id": op["id"], "method": "GET", "path": op["path"],
                       "source_url": op["source_url"], "discovery_request": discovery}
        record = {**base_record, "network_request_made": False}
        try:
            if op["method"] != "GET" or op["scope"] != "ML data read":
                raise SetupError("Only the documented ML data GET operations can run live.")
            if op["path"].startswith("/v2/loans{resourceId}"):
                raise SetupError("Published loan-balance path has a missing slash. Not guessed or called; inspect loan detail balances instead.")
            profile = self.config.get("profiles", {}).get(profile_key, {})
            base = sandbox_base(profile, op)
            token = env_value(profile.get("access_token_env"))
            consent = env_value(profile.get("consent_id_env"))
            params = self.parameters(op)
            path = op["path"]
            if "{" in path:
                if not self.ids.get(family) and family not in self.roots:
                    root_op = next(o for o in self.catalog["operations"]
                                   if o["scope"] == "ML data read" and o["path"] == root_path)
                    self.run(root_op, discovery=True)
                resource_id = self.ids.get(family)
                if not resource_id:
                    raise SetupError(f"No authorized {family} resourceId available; set resource_ids.{family} or inspect the live list response.")
                path = re.sub(r"\{[^}]+\}", lambda _: quote(str(resource_id), safe=""), path)
            headers = {"Accept": "application/json", "Authorization": "Bearer " + token,
                       "X-Request-ID": str(uuid.uuid4())}
            consent_name = next((p["name"] for p in params
                                 if p.get("in") == "header" and p["name"].lower() in {"consent-id", "consentid"}), "Consent-ID")
            headers[consent_name] = consent
            psu_env = profile.get("psu_ip_address_env")
            psu_required = any(p["name"].lower() == "psu-ip-address" and p.get("required") for p in params)
            if psu_env and os.environ.get(psu_env):
                headers["PSU-IP-Address"] = env_value(psu_env)
            elif psu_required:
                raise SetupError("This operation requires PSU-IP-Address; configure the actual caller IP through psu_ip_address_env.")
            query_names = {p["name"] for p in params if p.get("in") == "query"}
            today = dt.datetime.now(dt.timezone.utc).date()
            defaults = {"withBalance": "true", "bookingStatus": "both",
                        "dateFrom": str(today - dt.timedelta(days=30)), "dateTo": str(today)}
            query = {k: v for k, v in defaults.items() if k in query_names}
            configured_query = profile.get("query", {})
            unknown = set(configured_query) - {"withBalance", "bookingStatus", "dateFrom", "dateTo", "entryReferenceFrom"}
            if unknown:
                raise SetupError("Unknown query configuration key(s): " + ", ".join(sorted(unknown)))
            query.update({k: v for k, v in configured_query.items() if k in query_names})
            self._transport()
            # Prepared URL is signed after requests has encoded query parameters.
            prepared = self.session.prepare_request(self.requests.Request("GET", base + path, headers=headers, params=query))
            self.signer.sign(prepared)
            record.update(url=prepared.url, attempted_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                          response_signature_verified=False, first_page_only=True)
            settings = self.session.merge_environment_settings(prepared.url, {}, None, self.session.verify, self.session.cert)
            record["network_request_made"] = True
            response = self.session.send(prepared, timeout=self.timeout, allow_redirects=False, **settings)
            record.update(http_status=response.status_code, response_bytes=len(response.content),
                          content_type=response.headers.get("Content-Type"))
            try:
                body = response.json() if response.content else None
            except ValueError:
                body = response.text
            record["body"] = body
            expected = self._expected_root(op, family)
            shape_ok = isinstance(body, dict) and expected in body
            if expected in {"accounts", "cardAccounts", "balances"}:
                shape_ok = shape_ok and isinstance(body[expected], list)
            elif expected != "resourceId":
                shape_ok = shape_ok and isinstance(body[expected], dict)
            if not response.content:
                record["classification"] = "live_empty_response"
            elif 200 <= response.status_code < 300 and shape_ok:
                record["classification"] = "live_data_response"
                if op["path"] == root_path:
                    items = body.get(root_key, [])
                    first_id = next((item.get("resourceId") for item in items
                                     if isinstance(item, dict) and item.get("resourceId")), None)
                    if first_id and not self.ids.get(family):
                        self.ids[family] = first_id
            else:
                record["classification"] = "live_http_or_response_error"
        except SetupError as exc:
            record.update(classification="skipped_setup", message=str(exc))
        except self.requests.RequestException as exc:
            record.update(classification="live_transport_error", error_type=type(exc).__name__, message=str(exc))
        except (OSError, ValueError, UnicodeError) as exc:
            # Never print credential values or private-key contents.
            record.update(classification="local_configuration_error", error_type=type(exc).__name__,
                          message="Local request preparation failed; check certificate/key/CA files and configuration.")
        if op["path"] == root_path:
            self.roots[family] = record
        self.records.append(record)
        return record

    @staticmethod
    def _expected_root(op, family):
        if op["path"].endswith("/transactions"):
            return "cardTransactions" if family == "cards" else "transactions"
        if op["path"].endswith("/balances"):
            return "balances"
        if "{" not in op["path"]:
            return FAMILIES[family][1]
        return "cardAccount" if family == "cards" else "resourceId" if family == "savings" else "account"


def display(record):
    print("\n" + "=" * 78)
    print(record["operation_id"])
    print(f"{record['method']} {record['path']}")
    print("TYPE: " + record["classification"].upper())
    print("Source: " + record["source_url"])
    if "http_status" in record:
        print(f"LIVE HTTP {record['http_status']} | {record['response_bytes']} bytes")
    elif "http_status_documented" in record:
        print("Documented status: " + record["http_status_documented"])
    if "message" in record:
        print(record["message"])
    if "body" in record:
        print("<empty response body>" if record["body"] is None else
              json.dumps(record["body"], ensure_ascii=False, indent=2))
    elif "response_schemas" in record:
        for schema in record["response_schemas"]:
            print(f"Schema for {schema['status']} ({schema['content_type']}):")
            print(json.dumps(schema["schema"], ensure_ascii=False, indent=2))
            print("Documented response fields:")
            for field in schema["fields"]:
                print("  " + field)
        print("Complete schema: " + record["schema_file"])
    elif "documented_statuses" in record:
        print("Documented statuses: " + ", ".join(record["documented_statuses"]))


def save_results(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Local live output can contain customer data; create a private file.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")


def display_export_summary(result, path, stream=None):
    """Index the saved JSON by request without dumping response bodies."""
    stream = stream or sys.stdout
    records = result["records"]
    print("\nSaved JSON: " + str(Path(path).resolve()), file=stream)
    print(f"Included request outputs: {len(records)} records "
          f"({result['requested_operations']} selected operations)", file=stream)
    counts = {}
    for index, record in enumerate(records):
        classification = record["classification"]
        counts[classification] = counts.get(classification, 0) + 1
        location = f"records[{index}]"
        if "body" in record:
            location += ".body"
        elif "response_schemas" in record:
            location += ".response_schemas"
        discovery = " [discovery]" if record.get("discovery_request") else ""
        print(f"  [{index + 1:02d}] {record['method']:<6} {record['path']}"
              f" [{classification}]{discovery} -> {location}", file=stream)
    print("\nSummary: " + json.dumps(counts), file=stream)
    if result["mode"] == "examples":
        print("Published examples and synthetic illustrations are labeled in the JSON; "
              "these are not live responses.", file=stream)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["examples", "live"], default="examples")
    p.add_argument("--scope", choices=["all", "data"], help="Default: all 71 examples; 14 data reads in live mode.")
    p.add_argument("--only", help="Comma-separated reference slugs or entry numbers from --list.")
    p.add_argument("--list", action="store_true", help="List operations and exit without network requests.")
    p.add_argument("--status", help="Choose a documented response code, e.g. 400 (examples mode).")
    p.add_argument("--example-index", type=int, default=0, help="Zero-based published example variant.")
    p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    p.add_argument("--config", type=Path, help="Local sandbox JSON configuration for live GETs.")
    p.add_argument("--output", type=Path, help="JSON file path. Default: output/tbc_example_outputs.json (or tbc_live_outputs.json in live mode), relative to the script folder.")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of the human view.")
    p.add_argument("--show-bodies", action="store_true", help="Also print full response examples before the saved-file index.")
    args = p.parse_args(argv)
    try:
        if args.example_index < 0:
            raise SetupError("--example-index must be zero or greater.")
        if args.json and args.show_bodies:
            raise SetupError("Use --json or --show-bodies, not both.")
        catalog = read_json(args.catalog)
        scope = args.scope or ("data" if args.mode == "live" else "all")
        operations = select_operations(catalog, scope, args.only)
        if args.list:
            for op in operations:
                print(f"{op['id'].split('_', 1)[0]:>2}  {op['method']:<6} {op['path']}  [{op['reference_slug']}]")
            return 0
        if args.mode == "examples":
            records = [published_result(op, args.status, args.example_index) for op in operations]
        else:
            if args.status or args.example_index:
                raise SetupError("--status/--example-index select documentation examples, not live calls.")
            if any(op["scope"] != "ML data read" or op["method"] != "GET" for op in operations):
                raise SetupError("Live mode supports --scope data only. Payment/control APIs are examples-only.")
            if not args.config:
                raise SetupError("Live mode needs --config tbc_sandbox_config.json. See TBC_RUNNER_README.md.")
            # Bundled catalogue paths are relative to the runner directory, not the shell cwd.
            reader = SandboxReader(catalog, read_json(args.config), args.config.resolve().parent, ROOT)
            for op in operations:
                reader.run(op)
            records = reader.records
        result = {"mode": args.mode, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "requested_operations": len(operations), "records": records}
        output = args.output or ROOT / "output" / (
            "tbc_live_outputs.json" if args.mode == "live" else "tbc_example_outputs.json")
        save_results(output, result)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.show_bodies:
            for record in records:
                display(record)
        # Keep stdout valid JSON when --json is used; the CLI index goes to stderr.
        display_export_summary(result, output, stream=sys.stderr if args.json else sys.stdout)
        if args.mode == "live" and any(r["classification"] != "live_data_response" for r in records):
            return 1
        return 0
    except (SetupError, KeyError) as exc:
        print("Setup error: " + str(exc), file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
