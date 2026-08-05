#!/usr/bin/env python3
"""Safely inspect, check, or apply the fixed Paddle migration set."""

import argparse
import os
import shutil
import subprocess
import sys

from rollout_common import MIGRATION_FILES, migration_paths, missing_migrations


CHECK_SQL = """
SELECT CASE WHEN
  EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'subscription_events' AND column_name = 'paddle_event_id')
  AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'salons' AND column_name = 'paddle_subscription_updated_at')
  AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'salons' AND column_name = 'subscription_grace_ends_at')
  AND EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'subscription_events' AND indexname = 'idx_subscription_events_paddle_event_id')
THEN 'PASS' ELSE 'FAIL' END;
""".strip()

PRODUCTION_CONFIRMATION = "APPLY-PADDLE-MIGRATIONS"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check expected columns and index; make no changes.")
    mode.add_argument("--apply", action="store_true", help="Apply all known migrations in fixed order.")
    parser.add_argument("--environment", choices=("sandbox", "production"))
    parser.add_argument("--confirm-production", default="", help=argparse.SUPPRESS)
    return parser


def psql_command(database_url, *arguments):
    return ["psql", "-X", database_url, "--set=ON_ERROR_STOP=1", *arguments]


def main(argv=None):
    args = build_parser().parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL: missing", file=sys.stderr)
        return 2

    missing = missing_migrations()
    if missing:
        for filename in missing:
            print(f"Missing migration: {filename}", file=sys.stderr)
        return 2

    print("Migration order:")
    for filename in MIGRATION_FILES:
        print(f"  {filename}")

    if not args.check and not args.apply:
        print("DRY RUN: files validated; no database connection or changes made.")
        return 0

    if not args.environment:
        print("Check/apply requires an explicit --environment sandbox|production.", file=sys.stderr)
        return 2
    if shutil.which("psql") is None:
        print("psql: missing", file=sys.stderr)
        return 2
    if args.apply and args.environment == "production" and args.confirm_production != PRODUCTION_CONFIRMATION:
        print("Production apply requires the documented explicit confirmation.", file=sys.stderr)
        return 2

    try:
        if args.check:
            completed = subprocess.run(
                psql_command(database_url, "--tuples-only", "--no-align", "--command", CHECK_SQL),
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                print("Migration check failed; connection details were suppressed.", file=sys.stderr)
                return 1
            passed = completed.stdout.strip() == "PASS"
            print(f"Schema check: {'PASS' if passed else 'FAIL'}")
            return 0 if passed else 1

        for path in migration_paths():
            print(f"Applying: {path.name}")
            completed = subprocess.run(
                psql_command(database_url, "--file", str(path)),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if completed.returncode != 0:
                print(f"Migration failed: {path.name}; connection details were suppressed.", file=sys.stderr)
                return 1
        print("Migration apply: PASS")
        return 0
    except OSError:
        print("psql execution failed; connection details were suppressed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
