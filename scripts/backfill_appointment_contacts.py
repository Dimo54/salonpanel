"""Backfill compatibility snapshots without claiming original submission provenance.

Values copied by this utility reflect the currently linked client record. They
are marked ``legacy_backfill`` and are not proof of what a client originally
submitted when booking.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phone_utils import normalize_phone


DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 5000
DEFAULT_MAX_BATCHES = 1
MAX_BATCHES = 100
LEGACY_CONTACT_SOURCE = "legacy_backfill"

SELECT_BATCH_SQL = """
    SELECT a.id, c.name, c.phone, c.phone_normalized, c.email
    FROM appointments a
    JOIN clients c ON c.id = a.client_id
    WHERE a.contact_source IS NULL
    ORDER BY a.id
    LIMIT %s
"""

SELECT_LOCKED_BATCH_SQL = SELECT_BATCH_SQL.rstrip() + "\n    FOR UPDATE OF a SKIP LOCKED"

UPDATE_APPOINTMENT_SQL = """
    UPDATE appointments
    SET contact_name = COALESCE(contact_name, %s),
        contact_phone = COALESCE(contact_phone, %s),
        contact_phone_normalized = COALESCE(contact_phone_normalized, %s),
        contact_email = COALESCE(contact_email, %s),
        contact_source = %s
    WHERE id = %s AND contact_source IS NULL
"""


def positive_bounded(value, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("Value must be a positive integer.") from exc
    if parsed < 1 or parsed > maximum:
        raise argparse.ArgumentTypeError(f"Value must be between 1 and {maximum}.")
    return parsed


def database_url(argument_value=None):
    value = (argument_value or os.environ.get("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit(
            "DATABASE_URL is required. Pass --database-url or set DATABASE_URL."
        )
    return value


def normalized_client_phone(row):
    existing = (row.get("phone_normalized") or "").strip()
    if existing:
        return existing, False
    normalized, error = normalize_phone(row.get("phone"))
    return normalized, bool(error)


def load_batch(cursor, limit, lock_rows):
    cursor.execute(SELECT_LOCKED_BATCH_SQL if lock_rows else SELECT_BATCH_SQL, (limit,))
    return cursor.fetchall()


def update_appointment(cursor, row):
    normalized, invalid_phone = normalized_client_phone(row)
    cursor.execute(
        UPDATE_APPOINTMENT_SQL,
        (
            row.get("name"),
            row.get("phone"),
            normalized,
            row.get("email"),
            LEGACY_CONTACT_SOURCE,
            row["id"],
        ),
    )
    return invalid_phone


def run_backfill(connection, batch_size, max_batches, apply_changes):
    processed = 0
    invalid_phones = 0

    if not apply_changes:
        with connection.cursor() as cursor:
            rows = load_batch(cursor, batch_size * max_batches, lock_rows=False)
        connection.rollback()
        for row in rows:
            _, invalid_phone = normalized_client_phone(row)
            invalid_phones += int(invalid_phone)
        return len(rows), invalid_phones

    for _ in range(max_batches):
        try:
            with connection.cursor() as cursor:
                rows = load_batch(cursor, batch_size, lock_rows=True)
                if not rows:
                    connection.rollback()
                    break
                for row in rows:
                    invalid_phones += int(update_appointment(cursor, row))
            connection.commit()
            processed += len(rows)
        except Exception:
            connection.rollback()
            raise
    return processed, invalid_phones


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Backfill appointment contact snapshots from current client values. "
            "Dry-run is the default."
        )
    )
    parser.add_argument("--database-url", help="Explicit PostgreSQL connection URL.")
    parser.add_argument(
        "--batch-size",
        type=lambda value: positive_bounded(value, MAX_BATCH_SIZE),
        default=DEFAULT_BATCH_SIZE,
    )
    parser.add_argument(
        "--max-batches",
        type=lambda value: positive_bounded(value, MAX_BATCHES),
        default=DEFAULT_MAX_BATCHES,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply bounded updates. Without this flag, no rows are changed.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    url = database_url(args.database_url)
    connection = None
    try:
        connection = psycopg.connect(url, row_factory=dict_row)
        processed, invalid_phones = run_backfill(
            connection,
            args.batch_size,
            args.max_batches,
            args.apply,
        )
    except Exception:
        if connection is not None:
            connection.rollback()
        print("Appointment contact backfill failed; no sensitive details were printed.", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()

    mode = "Applied" if args.apply else "Dry run"
    print(
        f"{mode}: {processed} appointment rows considered; "
        f"{invalid_phones} phone values could not be normalized."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
