#!/usr/bin/env python3
"""Safely inspect, check, or apply the reliable-reminder migration."""

import argparse
import os
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rollout_common import missing_reminder_migrations, reminder_migration_paths


PRODUCTION_CONFIRMATION_PREFIX = "APPLY-REMINDER-MIGRATIONS"
HISTORY_CONVENTIONS = ("local-wall-clock", "utc", "legacy-past-only")
LOCAL_DOCKER_HOST = "127.0.0.1"
LOCAL_DOCKER_PORT = 55432
CHECK_SQL = """
WITH target AS (
  SELECT current_database() AS database_name, current_setting('TimeZone') AS timezone
), schema_check AS (
  SELECT
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'appointments' AND column_name = 'schedule_version' AND data_type = 'integer' AND is_nullable = 'NO') AS schedule_version,
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'appointments' AND column_name = 'schedule_started_at' AND data_type = 'timestamp without time zone' AND is_nullable = 'NO') AS schedule_started_at,
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'reminder_events')
      AND 7 = (SELECT count(*) FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'reminder_events'
                 AND ((column_name = 'salon_id' AND data_type = 'integer' AND is_nullable = 'NO')
                   OR (column_name = 'appointment_id' AND data_type = 'integer' AND is_nullable = 'NO')
                   OR (column_name = 'reminder_type' AND data_type = 'text' AND is_nullable = 'NO')
                   OR (column_name = 'schedule_version' AND data_type = 'integer' AND is_nullable = 'NO')
                   OR (column_name = 'state' AND data_type = 'text' AND is_nullable = 'NO')
                   OR (column_name = 'attempt_count' AND data_type = 'integer' AND is_nullable = 'NO')
                   OR (column_name = 'lease_expires_at' AND data_type = 'timestamp without time zone')))
      AS reminder_events,
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('public.reminder_events') AND contype = 'u' AND pg_get_constraintdef(oid) LIKE '%UNIQUE (appointment_id, reminder_type, schedule_version)%') AS reminder_unique,
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'reminder_events' AND indexname = 'idx_reminder_events_retry') AS retry_index,
    NOT EXISTS (
      SELECT 1 FROM email_events e JOIN appointments a ON a.id = e.appointment_id
      LEFT JOIN reminder_events r ON r.appointment_id = e.appointment_id
       AND r.reminder_type = CASE e.event_key WHEN 'reminder-24h' THEN 'reminder_24h' ELSE 'reminder_2h' END
       AND r.schedule_version = 1 AND r.state = 'successful'
      WHERE e.successful AND e.event_key IN ('reminder-24h', 'reminder-2h') AND r.id IS NULL
    ) AS legacy_backfill_complete
)
SELECT database_name, timezone, schedule_version, schedule_started_at,
       reminder_events, reminder_unique, retry_index, legacy_backfill_complete
FROM target CROSS JOIN schema_check;
""".strip()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Verify target identity, timezone, schema and backfill.")
    mode.add_argument("--apply", action="store_true", help="Apply the fixed migration after target confirmation.")
    parser.add_argument("--environment", choices=("sandbox", "staging", "production"))
    parser.add_argument("--expected-database", help="Exact current_database() value approved by the operator.")
    parser.add_argument("--expected-host", help="Exact PostgreSQL host approved by the operator.")
    parser.add_argument("--expected-port", type=int, help="Exact PostgreSQL port approved by the operator.")
    parser.add_argument("--expected-timezone", default=os.environ.get("APP_TIMEZONE", "Europe/Belgrade"))
    parser.add_argument("--history-timestamp-convention", choices=HISTORY_CONVENTIONS,
                        help="Required for apply when historical appointment timestamps need backfill.")
    parser.add_argument("--confirm-target", default="", help=argparse.SUPPRESS)
    return parser


def connection_environment(database_url, expected_timezone, history_convention=None):
    """Convert DATABASE_URL to libpq environment variables, never psql argv."""
    validate_timezone(expected_timezone)
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL is malformed")
    query = parse_qs(parsed.query)
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise ValueError("DATABASE_URL is malformed") from exc
    sslmode = query.get("sslmode", [None])[0]
    local_docker = parsed.hostname == LOCAL_DOCKER_HOST and port == LOCAL_DOCKER_PORT
    if local_docker:
        if sslmode not in {None, "disable"}:
            raise ValueError("local Docker test target must use sslmode=disable")
        sslmode = "disable"
    elif sslmode not in {None, "require", "verify-ca", "verify-full"}:
        raise ValueError("remote PostgreSQL target requires TLS")
    elif sslmode is None:
        sslmode = "require"
    # libpq honors many PG* variables. Remove every inherited one before
    # supplying the complete, parsed connection identity below.
    # DATABASE_URL is consumed here, not forwarded to the psql child.  The
    # child only receives the parsed libpq variables below, so the complete
    # credential-bearing URL cannot appear in its inherited environment.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("PG") and key != "DATABASE_URL"
    }
    environment.update({
        "PGHOST": parsed.hostname,
        "PGPORT": str(port),
        "PGDATABASE": unquote(parsed.path.lstrip("/")),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGCONNECT_TIMEOUT": "10",
        "PGSSLMODE": sslmode,
    })
    options = f"-c TimeZone={expected_timezone}"
    if history_convention:
        options += f" -c salonpanel.history_timestamp_convention={history_convention}"
    environment["PGOPTIONS"] = options
    return environment


def validate_timezone(expected_timezone):
    try:
        ZoneInfo(expected_timezone)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("expected timezone is invalid") from exc
    return expected_timezone


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def session_setup_sql(expected_timezone, history_convention=None):
    """Set rollout session state explicitly; poolers may discard PGOPTIONS."""
    timezone = validate_timezone(expected_timezone)
    statements = [f"SET TIME ZONE {sql_literal(timezone)}"]
    if history_convention:
        statements.append(
            "SELECT set_config('salonpanel.history_timestamp_convention', "
            f"{sql_literal(history_convention)}, false)"
        )
    return "; ".join(statements)


def psql_command(expected_timezone, *arguments, history_convention=None):
    # `--command` and `--file` are handled by one psql process/connection.
    # With ON_ERROR_STOP, a failed SET prevents the subsequent migration file.
    return [
        # Suppress the `SET` command tag so captured target/check output is
        # solely the explicitly requested query result.
        "psql", "-X", "--quiet", "--set=ON_ERROR_STOP=1",
        "--command", session_setup_sql(expected_timezone, history_convention),
        *arguments,
    ]


def run_psql(environment, expected_timezone, *arguments, history_convention=None, capture_output=False):
    return subprocess.run(psql_command(expected_timezone, *arguments, history_convention=history_convention),
                          check=False, shell=False, env=environment,
                          capture_output=capture_output, text=True)


def target_confirmation(environment, expected_database, expected_host, expected_port, expected_timezone):
    """Confirm DB/session facts; client endpoint approval is checked separately.

    `inet_server_addr()` and `inet_server_port()` describe PostgreSQL inside its
    network namespace, not the NAT-facing host/port that libpq contacted.
    They therefore cannot validate a Docker-published client endpoint.
    """
    completed = run_psql(environment, expected_timezone, "--tuples-only", "--no-align", "--field-separator=|",
                         "--command", "SELECT current_database(), current_setting('TimeZone')", capture_output=True)
    if completed.returncode:
        return False, "Target identity check failed; connection details were suppressed."
    actual = completed.stdout.strip().split("|")
    if len(actual) != 2 or actual[0] != expected_database or actual[1] != expected_timezone:
        return False, "Target identity or timezone did not match operator approval."
    # This is an explicit operator/configuration check, not a cryptographic
    # attestation of remote server identity. TLS verification is what binds a
    # remote endpoint to its certificate where verify-full is configured.
    if environment["PGHOST"] != expected_host or environment["PGPORT"] != str(expected_port):
        return False, "Configured host or port did not match operator approval."
    return True, ""


def main(argv=None):
    args = build_parser().parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL: missing", file=sys.stderr)
        return 2
    missing = missing_reminder_migrations()
    if missing:
        for filename in missing:
            print(f"Missing migration: {filename}", file=sys.stderr)
        return 2
    if not args.check and not args.apply:
        print("DRY RUN: files validated; no database connection or changes made.")
        return 0
    if (not args.environment or not args.expected_database or not args.expected_host
            or not args.expected_port or shutil.which("psql") is None):
        print("Check/apply requires psql, --environment, --expected-database, --expected-host and --expected-port.", file=sys.stderr)
        return 2
    if args.apply and not args.history_timestamp_convention:
        print("Apply requires --history-timestamp-convention; do not guess historical timestamps.", file=sys.stderr)
        return 2
    expected_confirmation = f"{PRODUCTION_CONFIRMATION_PREFIX}:{args.environment}:{args.expected_database}"
    if args.apply and args.confirm_target != expected_confirmation:
        print("Apply requires explicit environment and database confirmation.", file=sys.stderr)
        return 2
    try:
        environment = connection_environment(database_url, args.expected_timezone, args.history_timestamp_convention)
    except ValueError:
        print("DATABASE_URL is malformed.", file=sys.stderr)
        return 2
    if (environment["PGHOST"] != args.expected_host or environment["PGPORT"] != str(args.expected_port)
            or environment["PGDATABASE"] != args.expected_database):
        print("Configured database, host, or port did not match operator approval.", file=sys.stderr)
        return 2
    matches, message = target_confirmation(
        environment, args.expected_database, args.expected_host, args.expected_port, args.expected_timezone,
    )
    if not matches:
        print(message, file=sys.stderr)
        return 1
    if args.check:
        completed = run_psql(environment, args.expected_timezone, "--tuples-only", "--no-align", "--field-separator=|", "--command", CHECK_SQL, capture_output=True)
        values = completed.stdout.strip().split("|") if not completed.returncode else []
        if len(values) != 8 or values[0] != args.expected_database or values[1] != args.expected_timezone or any(value != "t" for value in values[2:]):
            print("Reminder migration check failed; schema, backfill, target, or timezone is incomplete.", file=sys.stderr)
            return 1
        print("Schema check: PASS")
        return 0
    for path in reminder_migration_paths():
        completed = run_psql(
            environment,
            args.expected_timezone,
            "--file",
            str(path),
            history_convention=args.history_timestamp_convention,
        )
        if completed.returncode:
            print(f"Migration failed: {path.name}; database diagnostics were suppressed.", file=sys.stderr)
            return 1
    print("Reminder migration apply: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
