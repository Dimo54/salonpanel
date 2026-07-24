import inspect
import io
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app
from phone_utils import INVALID_PHONE_MESSAGE, normalize_phone
from scripts import backfill_appointment_contacts


class DryRunCursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        self.statements.append((" ".join(query.split()), params))

    def fetchall(self):
        return self.rows


class DryRunConnection:
    def __init__(self, rows):
        self.cursor_instance = DryRunCursor(rows)
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self.cursor_instance

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class ContactSnapshotTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()

    def test_serbian_local_phone_normalizes_to_e164(self):
        normalized, error = normalize_phone("061 123 45 67")
        self.assertEqual(normalized, "+381611234567")
        self.assertIsNone(error)

    def test_international_phone_prefixes_normalize_identically(self):
        plus_value, plus_error = normalize_phone("+381 61 123 45 67")
        international_value, international_error = normalize_phone("00381 61 123 45 67")
        self.assertIsNone(plus_error)
        self.assertIsNone(international_error)
        self.assertEqual(plus_value, "+381611234567")
        self.assertEqual(international_value, plus_value)

    def test_invalid_phone_is_rejected_without_an_exception(self):
        for value in ("123", "not a phone"):
            with self.subTest(value=value):
                normalized, error = normalize_phone(value)
                self.assertIsNone(normalized)
                self.assertEqual(error, INVALID_PHONE_MESSAGE)

    def test_appointment_queries_prefer_snapshot_values(self):
        expected_expressions = {
            salon_app.appointment_conflict: ("COALESCE(a.contact_name, c.name)",),
            salon_app.dashboard: (
                "COALESCE(a.contact_name, c.name)",
                "COALESCE(a.contact_phone, c.phone)",
            ),
            salon_app.appointments: (
                "COALESCE(a.contact_name, c.name)",
                "COALESCE(a.contact_phone, c.phone)",
            ),
            salon_app.appointment_edit: (
                "COALESCE(a.contact_name, c.name)",
                "COALESCE(a.contact_phone, c.phone)",
                "COALESCE(a.contact_email, c.email)",
            ),
            salon_app.appointment_calendar: (
                "COALESCE(a.contact_name, c.name)",
                "COALESCE(a.contact_phone, c.phone)",
            ),
            salon_app.appointment_email_data: (
                "COALESCE(a.contact_name, c.name)",
                "COALESCE(a.contact_phone, c.phone)",
                "COALESCE(a.contact_email, c.email)",
            ),
            salon_app.booking_success: ("COALESCE(a.contact_name, c.name)",),
            salon_app.export_appointments: (
                "COALESCE(a.contact_name, c.name)",
                "COALESCE(a.contact_phone, c.phone)",
                "COALESCE(a.contact_email, c.email)",
            ),
            salon_app.super_admin_salon_detail: ("COALESCE(a.contact_name, c.name)",),
        }
        for reader, expressions in expected_expressions.items():
            source = inspect.getsource(reader)
            for expression in expressions:
                with self.subTest(reader=reader.__name__, expression=expression):
                    self.assertIn(expression, source)

    def test_null_snapshot_queries_fall_back_to_canonical_values(self):
        sources = "\n".join(
            inspect.getsource(reader)
            for reader in (
                salon_app.appointment_conflict,
                salon_app.dashboard,
                salon_app.appointments,
                salon_app.appointment_edit,
                salon_app.appointment_calendar,
                salon_app.appointment_email_data,
                salon_app.booking_success,
                salon_app.export_appointments,
                salon_app.super_admin_salon_detail,
            )
        )
        for snapshot, canonical in (
            ("a.contact_name", "c.name"),
            ("a.contact_phone", "c.phone"),
            ("a.contact_email", "c.email"),
        ):
            with self.subTest(snapshot=snapshot):
                self.assertRegex(
                    sources,
                    re.compile(
                        rf"COALESCE\(\s*{re.escape(snapshot)}\s*,\s*{re.escape(canonical)}\s*\)"
                    ),
                )

    def test_appointment_email_data_selects_snapshot_email_first(self):
        selected = {
            "id": 9,
            "client_name": "Snapshot Name",
            "client_phone": "+381611234567",
            "client_email": "snapshot@example.test",
        }
        with (
            salon_app.app.test_request_context("/"),
            patch.object(salon_app, "db_query", return_value=selected) as db_query,
        ):
            data = salon_app.appointment_email_data(9, 4)
        query = db_query.call_args.args[0]
        self.assertIn("COALESCE(a.contact_email, c.email) AS client_email", query)
        self.assertEqual(data["client_email"], "snapshot@example.test")

    def test_reminder_message_receives_snapshot_email_data(self):
        data = {
            "client_name": "Snapshot Name",
            "client_email": "snapshot@example.test",
        }
        with (
            patch.object(salon_app, "appointment_email_data", return_value=data),
            patch.object(
                salon_app,
                "send_appointment_message",
                return_value=(True, None),
            ) as send_message,
            patch.object(salon_app, "db_execute"),
        ):
            sent, error = salon_app.send_appointment_event(
                9,
                4,
                "reminder-24h",
                "reminder_24h",
            )
        self.assertTrue(sent)
        self.assertIsNone(error)
        send_message.assert_called_once_with(data, "reminder_24h")
        self.assertEqual(send_message.call_args.args[0]["client_email"], "snapshot@example.test")

    def test_public_success_uses_snapshot_name(self):
        salon = {"id": 4, "slug": "test-salon"}
        appointment = {
            "id": 9,
            "status": "scheduled",
            "client_name": "Snapshot Name",
            "service_name": "Usluga",
            "worker_name": "Radnik",
            "date": "2030-01-10",
            "time": "10:00",
            "duration_minutes": 30,
        }
        with patch.object(
            salon_app,
            "db_query",
            side_effect=[salon, appointment],
        ) as db_query:
            response = self.client.get("/s/test-salon/zakazi/uspesno/token")
        appointment_query = db_query.call_args_list[1].args[0]
        self.assertIn("COALESCE(a.contact_name, c.name) AS client_name", appointment_query)
        self.assertIn("Snapshot Name", response.get_data(as_text=True))

    def test_client_csv_stays_canonical(self):
        source = inspect.getsource(salon_app.export_clients)
        self.assertIn("SELECT c.id, c.name, c.phone, c.email", source)
        self.assertNotIn("a.contact_name", source)
        self.assertNotIn("a.contact_phone", source)
        self.assertNotIn("a.contact_email", source)

    def test_contact_source_values_are_prepared_without_a_database_check(self):
        self.assertEqual(
            salon_app.CONTACT_SOURCES,
            ("public_submission", "admin_submission", "legacy_backfill"),
        )
        migration = Path(
            "migrations/20260724_add_appointment_contact_snapshots.sql"
        ).read_text(encoding="utf-8")
        self.assertNotIn("CHECK", migration.upper())

    def test_migration_contains_required_additive_columns_and_indexes(self):
        migration = Path(
            "migrations/20260724_add_appointment_contact_snapshots.sql"
        ).read_text(encoding="utf-8")
        for column in (
            "contact_name",
            "contact_phone",
            "contact_phone_normalized",
            "contact_email",
            "contact_source",
            "privacy_consent_at",
            "marketing_consent",
        ):
            with self.subTest(table="appointments", column=column):
                self.assertRegex(
                    migration,
                    rf"ADD COLUMN IF NOT EXISTS {column}\b",
                )
        self.assertRegex(migration, r"ADD COLUMN IF NOT EXISTS phone_normalized\b")
        self.assertIn(
            "ON clients(salon_id, phone_normalized)",
            migration,
        )
        self.assertIn("ON appointments(client_id)", migration)
        self.assertNotRegex(migration.upper(), r"\bUPDATE\b|\bINSERT\b")
        self.assertNotIn("NOT NULL", migration.upper())

    def test_fresh_install_schema_contains_required_columns_and_indexes(self):
        source = Path("app.py").read_text(encoding="utf-8")
        clients_schema = re.search(
            r"CREATE TABLE IF NOT EXISTS clients \((.*?)\n    \);",
            source,
            re.DOTALL,
        ).group(1)
        appointments_schema = re.search(
            r"CREATE TABLE IF NOT EXISTS appointments \((.*?)\n    \);",
            source,
            re.DOTALL,
        ).group(1)
        self.assertRegex(clients_schema, r"\bphone_normalized TEXT\b")
        for column in (
            "contact_name",
            "contact_phone",
            "contact_phone_normalized",
            "contact_email",
            "contact_source",
            "privacy_consent_at",
            "marketing_consent",
        ):
            with self.subTest(column=column):
                self.assertRegex(appointments_schema, rf"\b{column}\b")
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS idx_clients_salon_phone_normalized "
            "ON clients(salon_id, phone_normalized)",
            " ".join(source.split()),
        )
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS idx_appointments_client_id "
            "ON appointments(client_id)",
            " ".join(source.split()),
        )

    def test_backfill_dry_run_is_bounded_and_does_not_update(self):
        rows = [
            {
                "id": 9,
                "name": "Legacy Client",
                "phone": "061 123 45 67",
                "phone_normalized": None,
                "email": "legacy@example.test",
            }
        ]
        connection = DryRunConnection(rows)
        processed, invalid = backfill_appointment_contacts.run_backfill(
            connection,
            batch_size=10,
            max_batches=2,
            apply_changes=False,
        )
        self.assertEqual(processed, 1)
        self.assertEqual(invalid, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(
            connection.cursor_instance.statements[0][1],
            (20,),
        )
        self.assertFalse(
            any(
                statement.startswith("UPDATE")
                for statement, _ in connection.cursor_instance.statements
            )
        )

    def test_backfill_is_idempotent_and_never_copies_consent(self):
        select_sql = backfill_appointment_contacts.SELECT_BATCH_SQL
        update_sql = backfill_appointment_contacts.UPDATE_APPOINTMENT_SQL
        self.assertIn("WHERE a.contact_source IS NULL", select_sql)
        self.assertIn("WHERE id = %s AND contact_source IS NULL", update_sql)
        for column in (
            "contact_name",
            "contact_phone",
            "contact_phone_normalized",
            "contact_email",
        ):
            self.assertRegex(
                update_sql,
                rf"{column} = COALESCE\({column}, %s\)",
            )
        self.assertNotIn("privacy_consent", update_sql)
        self.assertNotIn("marketing_consent", update_sql)

    def test_backfill_requires_configuration_and_prints_no_rows(self):
        with (
            patch.object(backfill_appointment_contacts.os.environ, "get", return_value=None),
            self.assertRaisesRegex(SystemExit, "DATABASE_URL is required"),
        ):
            backfill_appointment_contacts.database_url()

        output = io.StringIO()
        error_output = io.StringIO()
        connection = DryRunConnection(
            [
                {
                    "id": 9,
                    "name": "Legacy Client",
                    "phone": "061 123 45 67",
                    "phone_normalized": None,
                    "email": "legacy@example.test",
                }
            ]
        )
        with (
            patch.object(
                backfill_appointment_contacts.psycopg,
                "connect",
                return_value=connection,
            ),
            redirect_stdout(output),
            redirect_stderr(error_output),
        ):
            result = backfill_appointment_contacts.main(
                ["--database-url", "postgresql://test:test@localhost/test"]
            )
        self.assertEqual(result, 0)
        self.assertNotIn("Legacy Client", output.getvalue())
        self.assertNotIn("legacy@example.test", output.getvalue())
        self.assertEqual(error_output.getvalue(), "")
        self.assertEqual(connection.closes, 1)


if __name__ == "__main__":
    unittest.main()
