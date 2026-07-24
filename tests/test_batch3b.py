import io
import inspect
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import get_flashed_messages

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app
from scripts import backfill_client_phone_normalized


class AtomicCursor:
    def __init__(
        self,
        candidates=None,
        new_client_id=70,
        appointment_id=90,
        fail_on=None,
        events=None,
    ):
        self.candidates = list(candidates or [])
        self.new_client_id = new_client_id
        self.appointment_id = appointment_id
        self.fail_on = fail_on
        self.events = events if events is not None else []
        self.statements = []
        self.current_one = None
        self.current_all = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        self.statements.append((sql, params))
        self.events.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("simulated database failure")
        self.current_one = None
        self.current_all = []
        if sql.startswith("SELECT * FROM workers"):
            self.current_one = {"id": 2, "salon_id": 1, "active": True}
        elif sql.startswith("SELECT id FROM clients"):
            self.current_all = list(self.candidates)
        elif sql.startswith("INSERT INTO clients"):
            self.current_one = {"id": self.new_client_id}
        elif sql.startswith(("INSERT INTO appointments", "UPDATE appointments")):
            self.current_one = {"id": self.appointment_id}

    def fetchone(self):
        return self.current_one

    def fetchall(self):
        return self.current_all


class AtomicConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class BackfillCursor:
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


class BackfillConnection:
    def __init__(self, batches):
        self.batches = list(batches)
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        rows = self.batches.pop(0) if self.batches else []
        cursor = BackfillCursor(rows)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class Batch3BTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()
        self.salon = {
            "id": 1,
            "name": "Test salon",
            "slug": "test-salon",
            "subscription_status": "active",
            "booking_mode": "automatic",
            "booking_min_notice_minutes": 0,
            "booking_max_days": 90,
        }
        self.future_date = date.today() + timedelta(days=7)

    def csrf_token(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "batch3b-csrf"
        return "batch3b-csrf"

    def booking_context(self, form_data=None):
        return {
            "services": [],
            "booking_data": {},
            "settings": {
                **salon_app.DEFAULT_SALON_SETTINGS,
                "business_name": self.salon["name"],
                "booking_min_notice_minutes": 0,
                "booking_max_days": 90,
                "cancellation_notice_hours": 24,
            },
            "salon": self.salon,
            "form_data": form_data or {},
        }

    def persist_new(
        self,
        cursor,
        contact_source="public_submission",
        privacy_consent=True,
        marketing_consent=True,
        availability_error=None,
    ):
        connection = AtomicConnection(cursor)
        self.last_connection = connection
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(
                salon_app,
                "worker_service_assignment",
                return_value={"worker_id": 2, "service_id": 3},
            ),
            patch.object(
                salon_app,
                "appointment_availability_error",
                return_value=availability_error,
            ),
        ):
            result = salon_app.persist_appointment_locked(
                self.salon,
                service_id=3,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                price=1500,
                status="scheduled",
                source="public" if contact_source == "public_submission" else "admin",
                notes="Appointment note",
                contact_name="Submitted Name",
                contact_phone="061 123 45 67",
                contact_phone_normalized="+381611234567",
                contact_email="submitted@example.test",
                contact_source=contact_source,
                privacy_consent=privacy_consent,
                marketing_consent=marketing_consent,
                public_request=contact_source == "public_submission",
                public_token="public-token",
            )
        return connection, result

    @staticmethod
    def statement(cursor, prefix):
        return next(item for item in cursor.statements if item[0].startswith(prefix))

    def test_exact_phone_and_email_match_reuses_one_unchanged_client(self):
        cursor = AtomicCursor(candidates=[{"id": 44}])
        connection, (appointment_id, error) = self.persist_new(cursor)
        self.assertEqual(appointment_id, 90)
        self.assertIsNone(error)
        self.assertEqual(connection.commits, 1)
        self.assertFalse(any(sql.startswith("INSERT INTO clients") for sql, _ in cursor.statements))
        self.assertFalse(any(sql.startswith("UPDATE clients") for sql, _ in cursor.statements))
        appointment_params = self.statement(cursor, "INSERT INTO appointments")[1]
        self.assertEqual(appointment_params[1], 44)
        self.assertEqual(
            appointment_params[14:19],
            (
                "Submitted Name",
                "061 123 45 67",
                "+381611234567",
                "submitted@example.test",
                "public_submission",
            ),
        )

    def test_same_phone_with_different_email_creates_new_client(self):
        cursor = AtomicCursor(candidates=[], new_client_id=71)
        _, (appointment_id, error) = self.persist_new(cursor)
        self.assertEqual(appointment_id, 90)
        self.assertIsNone(error)
        candidate_params = self.statement(cursor, "SELECT id FROM clients")[1]
        self.assertEqual(
            candidate_params,
            (1, "+381611234567", "submitted@example.test"),
        )
        self.assertTrue(any(sql.startswith("INSERT INTO clients") for sql, _ in cursor.statements))

    def test_same_email_with_different_phone_creates_new_client(self):
        cursor = AtomicCursor(candidates=[])
        self.persist_new(cursor)
        candidate_sql, candidate_params = self.statement(cursor, "SELECT id FROM clients")
        self.assertIn("phone_normalized = %s", candidate_sql)
        self.assertEqual(candidate_params[1], "+381611234567")
        self.assertTrue(any(sql.startswith("INSERT INTO clients") for sql, _ in cursor.statements))

    def test_name_only_never_participates_in_client_matching(self):
        cursor = AtomicCursor(candidates=[])
        self.persist_new(cursor)
        candidate_sql, candidate_params = self.statement(cursor, "SELECT id FROM clients")
        self.assertNotIn("name", candidate_sql.lower())
        self.assertNotIn("Submitted Name", candidate_params)
        self.assertTrue(any(sql.startswith("INSERT INTO clients") for sql, _ in cursor.statements))

    def test_duplicate_exact_matches_create_new_client_instead_of_arbitrary_reuse(self):
        cursor = AtomicCursor(candidates=[{"id": 40}, {"id": 41}], new_client_id=72)
        self.persist_new(cursor)
        appointment_params = self.statement(cursor, "INSERT INTO appointments")[1]
        self.assertEqual(appointment_params[1], 72)
        self.assertTrue(any(sql.startswith("INSERT INTO clients") for sql, _ in cursor.statements))

    def test_new_canonical_client_has_normalized_identity_and_no_consent(self):
        cursor = AtomicCursor(candidates=[])
        self.persist_new(cursor)
        client_sql, client_params = self.statement(cursor, "INSERT INTO clients")
        self.assertEqual(
            client_params,
            (
                1,
                "Submitted Name",
                "061 123 45 67",
                "+381611234567",
                "submitted@example.test",
            ),
        )
        self.assertIn("notes, privacy_consent_at, marketing_consent", client_sql)
        self.assertIn("NULL, NULL, FALSE", client_sql)

    def test_public_appointment_stores_snapshots_and_appointment_consent(self):
        cursor = AtomicCursor(candidates=[{"id": 44}])
        self.persist_new(cursor, privacy_consent=True, marketing_consent=True)
        params = self.statement(cursor, "INSERT INTO appointments")[1]
        self.assertEqual(params[14], "Submitted Name")
        self.assertEqual(params[15], "061 123 45 67")
        self.assertEqual(params[16], "+381611234567")
        self.assertEqual(params[17], "submitted@example.test")
        self.assertEqual(params[18], "public_submission")
        self.assertIsNotNone(params[19])
        self.assertIs(params[20], True)

    def test_admin_creation_stores_snapshot_without_fabricated_consent(self):
        cursor = AtomicCursor(candidates=[{"id": 44}])
        self.persist_new(
            cursor,
            contact_source="admin_submission",
            privacy_consent=False,
            marketing_consent=False,
        )
        params = self.statement(cursor, "INSERT INTO appointments")[1]
        self.assertEqual(params[18], "admin_submission")
        self.assertIsNone(params[19])
        self.assertIs(params[20], False)
        self.assertFalse(any(sql.startswith("UPDATE clients") for sql, _ in cursor.statements))

    def test_admin_edit_keeps_client_and_updates_only_appointment_snapshot(self):
        cursor = AtomicCursor(appointment_id=8)
        connection = AtomicConnection(cursor)
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "worker_service_assignment", return_value={"worker_id": 2}),
            patch.object(salon_app, "appointment_availability_error", return_value=None),
        ):
            appointment_id, error = salon_app.persist_appointment_locked(
                self.salon,
                service_id=3,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                price=1500,
                status="scheduled",
                source="admin",
                notes="Changed note",
                client_id=55,
                contact_name="Edited Name",
                contact_phone="062 123 45 67",
                contact_phone_normalized="+381621234567",
                contact_email="edited@example.test",
                contact_source="admin_submission",
                appointment_id=8,
            )
        self.assertEqual(appointment_id, 8)
        self.assertIsNone(error)
        self.assertEqual(connection.commits, 1)
        self.assertFalse(any("advisory" in sql for sql, _ in cursor.statements))
        self.assertFalse(any("FROM clients" in sql for sql, _ in cursor.statements))
        self.assertFalse(any("INTO clients" in sql for sql, _ in cursor.statements))
        update_sql, params = self.statement(cursor, "UPDATE appointments")
        self.assertEqual(params[0], 55)
        self.assertEqual(params[9:14], (
            "Edited Name",
            "062 123 45 67",
            "+381621234567",
            "edited@example.test",
            "admin_submission",
        ))
        self.assertNotIn("privacy_consent_at", update_sql)
        self.assertNotIn("marketing_consent", update_sql)

    def test_invalid_public_phone_is_rejected_before_booking_writes(self):
        with (
            patch.object(salon_app, "db_query", return_value=self.salon),
            patch.object(salon_app, "rate_limit_exceeded", return_value=False),
            patch.object(salon_app, "worker_service_assignment") as assignment,
            patch.object(salon_app, "persist_appointment_locked") as persist,
            patch.object(
                salon_app,
                "public_booking_context",
                side_effect=lambda salon, form_data=None: self.booking_context(form_data),
            ),
        ):
            response = self.client.post(
                "/s/test-salon/zakazi",
                data={
                    "client_name": "Submitted Name",
                    "client_phone": "not-a-phone",
                    "client_email": "submitted@example.test",
                    "service_id": "3",
                    "worker_id": "2",
                    "date": self.future_date.isoformat(),
                    "time": "10:00",
                    "privacy_accepted": "on",
                    "csrf_token": self.csrf_token(),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Unesite ispravan broj telefona.", response.get_data(as_text=True))
        assignment.assert_not_called()
        persist.assert_not_called()

    def test_valid_public_route_passes_normalized_snapshot_and_consent(self):
        with (
            patch.object(
                salon_app,
                "db_query",
                side_effect=[self.salon, {"public_token": "public-token"}],
            ),
            patch.object(salon_app, "rate_limit_exceeded", return_value=False),
            patch.object(
                salon_app,
                "worker_service_assignment",
                return_value={"worker_duration_minutes": 30, "worker_price": 1500},
            ),
            patch.object(
                salon_app,
                "persist_appointment_locked",
                return_value=(90, None),
            ) as persist,
            patch.object(salon_app, "send_appointment_event", return_value=(True, None)),
            patch.object(salon_app, "appointment_email_data", return_value={}),
            patch.object(salon_app, "send_salon_new_request", return_value=(True, None)),
            patch.object(salon_app, "db_execute"),
        ):
            response = self.client.post(
                "/s/test-salon/zakazi",
                data={
                    "client_name": "  Submitted Name  ",
                    "client_phone": "  061 123 45 67  ",
                    "client_email": "  SUBMITTED@EXAMPLE.TEST  ",
                    "service_id": "3",
                    "worker_id": "2",
                    "date": self.future_date.isoformat(),
                    "time": "10:00",
                    "privacy_accepted": "on",
                    "marketing_accepted": "on",
                    "csrf_token": self.csrf_token(),
                },
            )
        self.assertEqual(response.status_code, 302)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["contact_name"], "Submitted Name")
        self.assertEqual(kwargs["contact_phone"], "061 123 45 67")
        self.assertEqual(kwargs["contact_phone_normalized"], "+381611234567")
        self.assertEqual(kwargs["contact_email"], "submitted@example.test")
        self.assertEqual(kwargs["contact_source"], "public_submission")
        self.assertTrue(kwargs["privacy_consent"])
        self.assertTrue(kwargs["marketing_consent"])

    def test_admin_create_and_edit_call_paths_do_not_resolve_before_persistence(self):
        assignment = {"worker_duration_minutes": 30, "worker_price": 1500}
        form = {
            "client_name": "Admin Name",
            "client_phone": "061 123 45 67",
            "client_email": "ADMIN@EXAMPLE.TEST",
            "service_id": "3",
            "worker_id": "2",
            "date": self.future_date.isoformat(),
            "time": "10:00",
            "status": "scheduled",
            "price": "1500",
        }
        with (
            salon_app.app.test_request_context("/appointments/new", method="POST", data=form),
            patch.object(salon_app, "current_salon", return_value=self.salon),
            patch.object(salon_app, "worker_service_assignment", return_value=assignment),
            patch.object(salon_app, "persist_appointment_locked", return_value=(90, None)) as persist,
            patch.object(salon_app, "send_appointment_event"),
        ):
            self.assertEqual(salon_app.save_appointment(), 90)
        create_kwargs = persist.call_args.kwargs
        self.assertIsNone(create_kwargs["client_id"])
        self.assertEqual(create_kwargs["contact_source"], "admin_submission")
        self.assertEqual(create_kwargs["contact_email"], "admin@example.test")
        self.assertNotIn("privacy_consent", create_kwargs)
        self.assertNotIn("marketing_consent", create_kwargs)

        previous = {
            "id": 8,
            "client_id": 55,
            "service_id": 3,
            "worker_id": 2,
            "date": self.future_date,
            "time": "10:00",
            "status": "scheduled",
        }
        with (
            salon_app.app.test_request_context("/appointments/8/edit", method="POST", data=form),
            patch.object(salon_app, "current_salon", return_value=self.salon),
            patch.object(salon_app, "db_query", return_value=previous),
            patch.object(salon_app, "worker_service_assignment", return_value=assignment),
            patch.object(salon_app, "persist_appointment_locked", return_value=(8, None)) as persist,
            patch.object(salon_app, "send_appointment_event"),
        ):
            self.assertEqual(salon_app.save_appointment(8), 8)
        edit_kwargs = persist.call_args.kwargs
        self.assertEqual(edit_kwargs["client_id"], 55)
        self.assertEqual(edit_kwargs["appointment_id"], 8)
        self.assertEqual(edit_kwargs["contact_source"], "admin_submission")

    def test_invalid_admin_phone_is_rejected_before_atomic_persistence(self):
        form = {
            "client_name": "Admin Name",
            "client_phone": "00381-invalid",
            "client_email": "admin@example.test",
            "service_id": "3",
            "worker_id": "2",
            "date": self.future_date.isoformat(),
            "time": "10:00",
            "status": "scheduled",
        }
        with (
            salon_app.app.test_request_context("/appointments/new", method="POST", data=form),
            patch.object(salon_app, "current_salon", return_value=self.salon),
            patch.object(salon_app, "worker_service_assignment") as assignment,
            patch.object(salon_app, "persist_appointment_locked") as persist,
        ):
            self.assertIsNone(salon_app.save_appointment())
            messages = [
                message for _, message in get_flashed_messages(with_categories=True)
            ]
        self.assertIn("Unesite ispravan broj telefona.", messages)
        assignment.assert_not_called()
        persist.assert_not_called()

    def test_final_conflict_happens_before_client_or_consent_writes(self):
        cursor = AtomicCursor(candidates=[])
        connection, (appointment_id, error) = self.persist_new(
            cursor,
            availability_error="Termin više nije dostupan. Izaberite drugo vreme.",
        )
        self.assertIsNone(appointment_id)
        self.assertIn("nije dostupan", error)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("advisory" in sql for sql, _ in cursor.statements))
        self.assertFalse(any("clients" in sql for sql, _ in cursor.statements))
        self.assertFalse(any("appointments" in sql for sql, _ in cursor.statements))

    def test_client_and_appointment_insert_exceptions_roll_back_everything(self):
        for failure in ("INSERT INTO clients", "INSERT INTO appointments"):
            with self.subTest(failure=failure):
                cursor = AtomicCursor(candidates=[], fail_on=failure)
                with self.assertRaisesRegex(RuntimeError, "simulated database failure"):
                    self.persist_new(cursor)
                self.assertEqual(self.last_connection.commits, 0)
                self.assertEqual(self.last_connection.rollbacks, 1)

    def test_lock_and_validation_order_precedes_client_resolution(self):
        events = []
        cursor = AtomicCursor(candidates=[], events=events)
        connection = AtomicConnection(cursor)

        def final_validation(*args, **kwargs):
            events.append("FINAL VALIDATION")
            return None

        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "worker_service_assignment", return_value={"worker_id": 2}),
            patch.object(
                salon_app,
                "appointment_availability_error",
                side_effect=final_validation,
            ),
        ):
            salon_app.persist_appointment_locked(
                self.salon,
                service_id=3,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                price=1500,
                status="scheduled",
                source="public",
                notes="",
                contact_name="Submitted Name",
                contact_phone="061 123 45 67",
                contact_phone_normalized="+381611234567",
                contact_email="submitted@example.test",
                contact_source="public_submission",
                privacy_consent=True,
            )
        worker_index = next(i for i, event in enumerate(events) if event.startswith("SELECT * FROM workers"))
        validation_index = events.index("FINAL VALIDATION")
        advisory_index = next(i for i, event in enumerate(events) if "pg_advisory_xact_lock" in event)
        client_index = next(i for i, event in enumerate(events) if event.startswith("SELECT id FROM clients"))
        insert_index = next(i for i, event in enumerate(events) if event.startswith("INSERT INTO clients"))
        self.assertLess(worker_index, validation_index)
        self.assertLess(validation_index, advisory_index)
        self.assertLess(advisory_index, client_index)
        self.assertLess(client_index, insert_index)

    def test_every_simulated_resolution_locks_before_checking_or_inserting(self):
        for candidates in ([], [{"id": 44}]):
            with self.subTest(candidates=candidates):
                cursor = AtomicCursor(candidates=candidates)
                salon_app.resolve_client_for_appointment(
                    cursor,
                    1,
                    "Submitted Name",
                    "061 123 45 67",
                    "+381611234567",
                    "submitted@example.test",
                )
                statements = [sql for sql, _ in cursor.statements]
                advisory_index = next(i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql)
                query_index = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT id FROM clients"))
                self.assertLess(advisory_index, query_index)

    def test_advisory_lock_key_is_stable_and_phone_scoped(self):
        first = salon_app.client_advisory_lock_key("+381611234567")
        second = salon_app.client_advisory_lock_key("+381611234567")
        other = salon_app.client_advisory_lock_key("+381621234567")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertGreaterEqual(first, -(2**31))
        self.assertLess(first, 2**31)

    def test_snapshot_email_remains_authoritative_after_canonical_change(self):
        selected = {
            "id": 9,
            "client_email": "snapshot@example.test",
            "client_name": "Snapshot Name",
        }
        with (
            salon_app.app.test_request_context("/"),
            patch.object(salon_app, "db_query", return_value=selected) as db_query,
        ):
            data = salon_app.appointment_email_data(9, 1)
        query = db_query.call_args.args[0]
        self.assertIn("COALESCE(a.contact_email, c.email)", query)
        self.assertEqual(data["client_email"], "snapshot@example.test")

    def test_client_list_remains_canonical(self):
        source = inspect.getsource(salon_app.clients)
        self.assertIn("SELECT c.*", source)
        self.assertNotIn("a.contact_name", source)
        self.assertNotIn("a.contact_phone", source)
        self.assertNotIn("a.contact_email", source)


class ClientPhoneBackfillTests(unittest.TestCase):
    def test_defaults_to_dry_run_and_requires_configuration(self):
        args = backfill_client_phone_normalized.build_parser().parse_args([])
        self.assertFalse(args.apply)
        with (
            patch.object(
                backfill_client_phone_normalized.os.environ,
                "get",
                return_value=None,
            ),
            self.assertRaisesRegex(SystemExit, "DATABASE_URL is required"),
        ):
            backfill_client_phone_normalized.database_url()

    def test_dry_run_is_bounded_rolls_back_and_preserves_invalid_nulls(self):
        rows = [
            {"id": 1, "phone": "061 123 45 67"},
            {"id": 2, "phone": "invalid"},
        ]
        connection = BackfillConnection([rows])
        processed, normalized, invalid = backfill_client_phone_normalized.run_backfill(
            connection,
            batch_size=10,
            max_batches=2,
            apply_changes=False,
        )
        self.assertEqual((processed, normalized, invalid), (2, 1, 1))
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.cursors[0].statements[0][1], (20,))
        self.assertFalse(
            any(
                sql.startswith("UPDATE")
                for cursor in connection.cursors
                for sql, _ in cursor.statements
            )
        )

    def test_apply_uses_bounded_locked_batches_and_updates_only_valid_numbers(self):
        rows = [
            {"id": 1, "phone": "061 123 45 67"},
            {"id": 2, "phone": "invalid"},
        ]
        connection = BackfillConnection([rows, []])
        processed, normalized, invalid = backfill_client_phone_normalized.run_backfill(
            connection,
            batch_size=2,
            max_batches=2,
            apply_changes=True,
        )
        self.assertEqual((processed, normalized, invalid), (2, 1, 1))
        self.assertEqual(connection.commits, 1)
        select_sql = connection.cursors[0].statements[0][0]
        self.assertIn("phone_normalized IS NULL", select_sql)
        self.assertIn("FOR UPDATE SKIP LOCKED", select_sql)
        updates = [
            (sql, params)
            for cursor in connection.cursors
            for sql, params in cursor.statements
            if sql.startswith("UPDATE clients")
        ]
        self.assertEqual(updates[0][1], ("+381611234567", 1))
        self.assertEqual(len(updates), 1)

    def test_backfill_is_idempotent_and_never_merges_or_prints_pii(self):
        self.assertIn(
            "WHERE id = %s AND phone_normalized IS NULL",
            " ".join(backfill_client_phone_normalized.UPDATE_CLIENT_SQL.split()),
        )
        source = Path("scripts/backfill_client_phone_normalized.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("DELETE FROM clients", source)
        self.assertNotIn("name, email", source)

        connection = BackfillConnection(
            [[{"id": 1, "phone": "061 123 45 67"}]]
        )
        output = io.StringIO()
        error_output = io.StringIO()
        with (
            patch.object(
                backfill_client_phone_normalized.psycopg,
                "connect",
                return_value=connection,
            ),
            redirect_stdout(output),
            redirect_stderr(error_output),
        ):
            result = backfill_client_phone_normalized.main(
                ["--database-url", "postgresql://test:test@localhost/test"]
            )
        self.assertEqual(result, 0)
        self.assertNotIn("061 123 45 67", output.getvalue())
        self.assertNotIn("postgresql://", output.getvalue())
        self.assertEqual(error_output.getvalue(), "")
        self.assertEqual(connection.closes, 1)


if __name__ == "__main__":
    unittest.main()
