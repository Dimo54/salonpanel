#!/usr/bin/env python3
"""Run non-destructive HTTP checks against an explicitly supplied deployment URL."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from rollout_common import validate_base_url


DEFAULT_TIMEOUT = 10.0
SECURITY_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Content-Security-Policy",
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Result:
    name: str
    passed: bool
    detail: str


def parse_expected_status(value: str) -> tuple[str, int]:
    path, separator, status = value.rpartition("=")
    if not separator or not path.startswith("/") or "?" in path or "#" in path:
        raise argparse.ArgumentTypeError("expected status must be /path=CODE")
    try:
        code = int(status)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("status must be an integer") from exc
    if not 100 <= code <= 599:
        raise argparse.ArgumentTypeError("status must be between 100 and 599")
    return path, code


def request_once(base_url, path, timeout, method="GET", headers=None, body=None):
    request = Request(urljoin(base_url + "/", path.lstrip("/")), data=body, headers=headers or {}, method=method)
    opener = build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    try:
        return response.status, response.headers
    finally:
        response.close()


def same_origin_location(base_url, location):
    if not location:
        return False
    target = urlsplit(urljoin(base_url + "/", location))
    origin = urlsplit(base_url)
    return target.scheme == origin.scheme and target.netloc == origin.netloc


def status_result(name, actual, expected):
    return Result(name, actual == expected, f"HTTP {actual} (expected {expected})")


def security_results(name, status, headers, require_no_store=False):
    results = []
    if 200 <= status < 400:
        for header in SECURITY_HEADERS:
            results.append(Result(f"{name} {header}", bool(headers.get(header)), "present" if headers.get(header) else "missing"))
        results.append(Result(f"{name} HSTS", bool(headers.get("Strict-Transport-Security")), "present" if headers.get("Strict-Transport-Security") else "missing"))
    if require_no_store:
        cache_control = headers.get("Cache-Control", "").lower()
        results.append(Result(f"{name} no-store", "no-store" in cache_control, "present" if "no-store" in cache_control else "missing"))
    return results


def authenticated_recovery_check(base_url, path, cookie_value, timeout):
    # The cookie is accepted only through an environment variable and is never returned or printed.
    status, headers = request_once(base_url, path, timeout, headers={"Cookie": cookie_value})
    results = [Result("authenticated recovery", status == 200, f"HTTP {status} (expected 200)")]
    results.extend(security_results("authenticated recovery", status, headers, require_no_store=True))
    return results


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", ""), help="Explicit deployed origin; BASE_URL may also be set.")
    parser.add_argument("--allow-http-localhost", action="store_true", help="Allow HTTP only for a local/private test address.")
    parser.add_argument("--salon-slug", help="Also GET the public booking page for this slug.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--expected-status", action="append", default=[], type=parse_expected_status, metavar="/PATH=CODE")
    parser.add_argument("--auth-cookie-env", help="Environment variable containing an explicit authenticated Cookie header value.")
    parser.add_argument("--recovery-path", default="/subscription")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.base_url:
        print("BASE_URL: missing", file=sys.stderr)
        return 2
    try:
        base_url = validate_base_url(args.base_url, args.allow_http_localhost)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.timeout > 60:
        print("timeout must be greater than 0 and no more than 60 seconds", file=sys.stderr)
        return 2
    if args.salon_slug and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.salon_slug):
        print("salon slug is malformed", file=sys.stderr)
        return 2
    recovery_parts = urlsplit(args.recovery_path)
    if (
        not args.recovery_path.startswith("/")
        or recovery_parts.netloc
        or recovery_parts.query
        or recovery_parts.fragment
    ):
        print("recovery path must be a local absolute path", file=sys.stderr)
        return 2

    expected_overrides = dict(args.expected_status)
    checks = [
        ("landing", "/", 200, False),
        ("robots", "/robots.txt", 200, False),
        ("sitemap", "/sitemap.xml", 200, False),
        ("privacy", "/privacy", 200, False),
        ("terms", "/terms", 200, False),
        ("login", "/login", 200, True),
        ("registration", "/register", 200, True),
    ]
    if args.salon_slug:
        checks.append(("public booking", f"/s/{quote(args.salon_slug)}/zakazi", 200, True))

    results = []
    try:
        for name, path, default_status, no_store in checks:
            status, headers = request_once(base_url, path, args.timeout)
            expected = expected_overrides.get(path, default_status)
            results.append(status_result(name, status, expected))
            results.extend(security_results(name, status, headers, require_no_store=no_store))

        status, headers = request_once(base_url, "/dashboard", args.timeout)
        location = headers.get("Location", "")
        protected_ok = status in {302, 303} and same_origin_location(base_url, location) and urlsplit(urljoin(base_url, location)).path == "/login"
        results.append(Result("unauthenticated protected route", protected_ok, f"HTTP {status}; safe login redirect {'yes' if protected_ok else 'no'}"))

        for label, headers in (
            ("webhook missing signature", {"Content-Type": "application/json"}),
            ("webhook invalid signature", {"Content-Type": "application/json", "Paddle-Signature": "ts=0;h1=invalid"}),
        ):
            status, _ = request_once(base_url, "/webhooks/paddle", args.timeout, method="POST", headers=headers, body=b"{}")
            results.append(status_result(label, status, expected_overrides.get("/webhooks/paddle", 401)))

        if args.auth_cookie_env:
            cookie_value = os.environ.get(args.auth_cookie_env, "")
            if not cookie_value:
                print(f"{args.auth_cookie_env}: missing", file=sys.stderr)
                return 2
            results.extend(authenticated_recovery_check(base_url, args.recovery_path, cookie_value, args.timeout))
    except (OSError, URLError, TimeoutError):
        print("HTTP smoke test failed due to a connection error; sensitive details were suppressed.", file=sys.stderr)
        return 1

    for result in results:
        print(f"{'PASS' if result.passed else 'FAIL'}  {result.name}: {result.detail}")
    failed = sum(not result.passed for result in results)
    print(f"Summary: {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
