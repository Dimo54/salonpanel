"""Safely populate missing canonical client phone normalization values."""

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

SELECT_DRY_RUN_SQL = """
    SELECT id, phone
    FROM clients
    WHERE phone_normalized IS NULL
    ORDER BY id
    LIMIT %s
"""

SELECT_LOCKED_BATCH_SQL = """
    SELECT id, phone
    FROM clients
    WHERE phone_normalized IS NULL AND id > %s
    ORDER BY id
    LIMIT %s
    FOR UPDATE SKIP LOCKED
"""

UPDATE_CLIENT_SQL = """
    UPDATE clients
    SET phone_normalized = %s
    WHERE id = %s AND phone_normalized IS NULL
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


def normalize_rows(rows):
    results = []
    invalid = 0
    for row in rows:
        normalized, error = normalize_phone(row.get("phone"))
        invalid += int(bool(error))
        results.append((row["id"], normalized))
    return results, invalid


def run_backfill(connection, batch_size, max_batches, apply_changes):
    processed = 0
    normalized_count = 0
    invalid_count = 0

    if not apply_changes:
        with connection.cursor() as cursor:
            cursor.execute(SELECT_DRY_RUN_SQL, (batch_size * max_batches,))
            rows = cursor.fetchall()
        connection.rollback()
        results, invalid_count = normalize_rows(rows)
        normalized_count = sum(1 for _, normalized in results if normalized)
        return len(rows), normalized_count, invalid_count

    last_id = 0
    for _ in range(max_batches):
        try:
            with connection.cursor() as cursor:
                cursor.execute(SELECT_LOCKED_BATCH_SQL, (last_id, batch_size))
                rows = cursor.fetchall()
                if not rows:
                    connection.rollback()
                    break
                results, batch_invalid = normalize_rows(rows)
                for client_id, normalized in results:
                    if normalized:
                        cursor.execute(UPDATE_CLIENT_SQL, (normalized, client_id))
                        normalized_count += 1
                invalid_count += batch_invalid
                last_id = rows[-1]["id"]
            connection.commit()
            processed += len(rows)
        except Exception:
            connection.rollback()
            raise
    return processed, normalized_count, invalid_count


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Populate missing client phone_normalized values. Dry-run is the default."
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
        processed, normalized_count, invalid_count = run_backfill(
            connection,
            args.batch_size,
            args.max_batches,
            args.apply,
        )
    except Exception:
        if connection is not None:
            connection.rollback()
        print(
            "Client phone normalization backfill failed; no sensitive details were printed.",
            file=sys.stderr,
        )
        return 1
    finally:
        if connection is not None:
            connection.close()

    mode = "Applied" if args.apply else "Dry run"
    print(
        f"{mode}: {processed} client rows considered; "
        f"{normalized_count} normalized; {invalid_count} invalid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
