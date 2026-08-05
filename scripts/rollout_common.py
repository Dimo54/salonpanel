"""Shared, side-effect-free rollout validation helpers."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILES = (
    "20260805_add_paddle_event_id.sql",
    "20260805_add_paddle_subscription_updated_at.sql",
    "20260805_add_subscription_grace_ends_at.sql",
)

ROLLOUT_ENVIRONMENT_VARIABLES = (
    "DATABASE_URL",
    "SECRET_KEY",
    "BREVO_API_KEY",
    "BREVO_SENDER_EMAIL",
    "REMINDER_SECRET",
    "PADDLE_ENVIRONMENT",
    "PADDLE_CLIENT_TOKEN",
    "PADDLE_API_KEY",
    "PADDLE_MONTHLY_PRICE_ID",
    "PADDLE_YEARLY_PRICE_ID",
    "PADDLE_WEBHOOK_SECRET",
    "PADDLE_WEBHOOK_TOLERANCE_SECONDS",
    "SUBSCRIPTION_GRACE_DAYS",
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def migration_paths(root: Path = REPOSITORY_ROOT) -> tuple[Path, ...]:
    return tuple(root / "migrations" / name for name in MIGRATION_FILES)


def missing_migrations(root: Path = REPOSITORY_ROOT) -> list[str]:
    return [path.name for path in migration_paths(root) if not path.is_file()]


def _integer_in_range(value: str, minimum: int, maximum: int) -> bool:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return False
    return minimum <= parsed <= maximum


def _integer_at_least(value: str, minimum: int) -> bool:
    try:
        return int(value) >= minimum
    except (TypeError, ValueError):
        return False


def validate_environment(mode: str, environ=None) -> list[tuple[str, str]]:
    """Return variable-name/status pairs without copying values into results."""
    if mode not in {"sandbox", "production"}:
        raise ValueError("mode must be sandbox or production")
    source = os.environ if environ is None else environ
    results = []
    for name in ROLLOUT_ENVIRONMENT_VARIABLES:
        value = str(source.get(name, "")).strip()
        status = "present"
        if not value:
            status = "missing"
        elif name == "DATABASE_URL":
            try:
                parsed = urlsplit(value)
                valid = parsed.scheme in {"postgres", "postgresql"} and bool(parsed.hostname) and bool(parsed.path.strip("/"))
            except ValueError:
                valid = False
            if not valid:
                status = "malformed"
        elif name == "BREVO_SENDER_EMAIL" and not EMAIL_RE.fullmatch(value):
            status = "malformed"
        elif name == "PADDLE_ENVIRONMENT" and value.lower() != mode:
            status = "malformed"
        elif name == "PADDLE_WEBHOOK_TOLERANCE_SECONDS" and not _integer_at_least(value, 0):
            status = "malformed"
        elif name == "SUBSCRIPTION_GRACE_DAYS" and not _integer_in_range(value, 0, 365):
            status = "malformed"
        results.append((name, status))
    return results


def validate_base_url(value: str, allow_http_localhost: bool = False) -> str:
    """Validate and normalize an operator-supplied smoke-test origin."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("BASE_URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("BASE_URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("BASE_URL must not contain credentials, a query, or a fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("BASE_URL must contain only an origin")

    hostname = parsed.hostname.lower()
    localhost = hostname == "localhost"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    local_address = bool(address and (address.is_loopback or address.is_private or address.is_link_local))
    if parsed.scheme != "https" and not (allow_http_localhost and (localhost or local_address)):
        raise ValueError("BASE_URL must use HTTPS (or explicitly allow local HTTP)")

    default_port = (parsed.scheme == "https" and port in {None, 443}) or (parsed.scheme == "http" and port in {None, 80})
    host = f"[{hostname}]" if ":" in hostname else hostname
    authority = host if default_port else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"
