#!/usr/bin/env python3
"""Run the installed reference smoke, optionally through local TLS termination."""
import argparse
import json
import runpy
import socket
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--all-clients", action="store_true")
    parser.add_argument("--local-proxy", action="store_true")
    args = parser.parse_args()
    if args.local_proxy:
        hostname = urlsplit(args.url).hostname
        original = socket.getaddrinfo

        def local_address(host, *positional, **kwargs):
            return original("127.0.0.1" if host == hostname else host, *positional, **kwargs)

        # Keep the URL, Host, Origin, certificate verification, and TLS SNI intact.
        socket.getaddrinfo = local_address
    check = runpy.run_path(str(Path(__file__).with_name("check_deployment.py")))["check"]
    result = check(args.url, args.all_clients)
    if result["model_sha256"] != "faa60238369de94c9ff19963243bc9ede32224b9d363bfb3bf66701705e8ce5b":
        raise RuntimeError("Deployed model checksum differs from the approved reference")
    if args.all_clients and len(result["clients_tested"]) != 20:
        raise RuntimeError("Expected all 20 existing demo customers")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
