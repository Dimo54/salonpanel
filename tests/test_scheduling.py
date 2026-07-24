import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class QueueCursor:
    def __init__(self, fetch_rows):
        self.fetch_rows = list(fetch_rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        self.statements.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.fetch_rows.pop(0) if self.fetch_rows else None


class QueueConnection:
    def __init__(self, fetch_rows):
        self.cursor_instance = QueueCursor(fetch_rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.salon = {
            "id": 1,
            "booking_min_notice_minutes": 0,
            "booking_max_days": 90,
        }
        self.future_date = date.today() + timedelta(days=7)

    def test_interval_boundaries_do_not_overlap(self):
        self.assertFalse(salon_app.intervals_overlap(600, 630, 630, 660))
        self.assertTrue(salon_app.intervals_overlap(600, 631, 630, 660))
        self.assertTrue(salon_app.intervals_overlap(620, 650, 600, 630))

    def test_availability_accepts_a_free_slot(self):
        with (
            patch.object(
                salon_app,
                "effective_worker_hours",
                return_value={"open": 9 * 60, "close": 17 * 60, "breaks": []},
            ),
            patch.object(salon_app, "worker_time_off_conflict", return_value=None),
            patch.object(salon_app, "appointment_conflict", return_value=None),
        ):
            error = salon_app.appointment_availability_error(
                self.salon,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                worker={"id": 2, "active": True},
            )
        self.assertIsNone(error)

    def test_availability_rejects_break_overlap(self):
        with patch.object(
            salon_app,
            "effective_worker_hours",
            return_value={
                "open": 9 * 60,
                "close": 17 * 60,
                "breaks": [(12 * 60, 13 * 60)],
            },
        ):
            error = salon_app.appointment_availability_error(
                self.salon,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="12:30",
                duration_minutes=30,
                worker={"id": 2, "active": True},
            )
        self.assertIn("pauzom", error)

    def test_availability_rejects_service_that_ends_after_working_hours(self):
        with patch.object(
            salon_app,
            "effective_worker_hours",
            return_value={"open": 9 * 60, "close": 17 * 60, "breaks": []},
        ):
            error = salon_app.appointment_availability_error(
                self.salon,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="16:40",
                duration_minutes=30,
                worker={"id": 2, "active": True},
            )
        self.assertIn("radno vreme", error)

    def test_availability_rejects_time_off_before_appointment_conflict_check(self):
        with (
            patch.object(
                salon_app,
                "effective_worker_hours",
                return_value={"open": 9 * 60, "close": 17 * 60, "breaks": []},
            ),
            patch.object(salon_app, "worker_time_off_conflict", return_value={"id": 4}),
            patch.object(salon_app, "appointment_conflict") as appointment_conflict,
        ):
            error = salon_app.appointment_availability_error(
                self.salon,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                worker={"id": 2, "active": True},
            )
        self.assertIn("ne radi", error)
        appointment_conflict.assert_not_called()

    def test_public_conflict_does_not_reveal_existing_client_name(self):
        existing_name = "Privatno Ime"
        with (
            patch.object(
                salon_app,
                "effective_worker_hours",
                return_value={"open": 9 * 60, "close": 17 * 60, "breaks": []},
            ),
            patch.object(salon_app, "worker_time_off_conflict", return_value=None),
            patch.object(
                salon_app,
                "appointment_conflict",
                return_value={"id": 44, "client_name": existing_name},
            ),
            patch.object(
                salon_app,
                "local_now",
                return_value=salon_app.datetime.combine(
                    self.future_date - timedelta(days=1),
                    salon_app.time(8, 0),
                    tzinfo=salon_app.ZoneInfo("Europe/Belgrade"),
                ),
            ),
        ):
            error = salon_app.appointment_availability_error(
                self.salon,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                public_request=True,
                worker={"id": 2, "active": True},
            )
        self.assertEqual(error, "Termin više nije dostupan. Izaberite drugo vreme.")
        self.assertNotIn(existing_name, error)

    def test_authenticated_conflict_keeps_detailed_client_information(self):
        existing_name = "Poznati Klijent"
        with (
            patch.object(
                salon_app,
                "effective_worker_hours",
                return_value={"open": 9 * 60, "close": 17 * 60, "breaks": []},
            ),
            patch.object(salon_app, "worker_time_off_conflict", return_value=None),
            patch.object(
                salon_app,
                "appointment_conflict",
                return_value={"id": 45, "client_name": existing_name},
            ),
        ):
            error = salon_app.appointment_availability_error(
                self.salon,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                public_request=False,
                worker={"id": 2, "active": True},
            )
        self.assertIn(existing_name, error)

    def test_appointment_conflict_counts_pending_and_scheduled_and_locks_rows(self):
        existing = {
            "id": 44,
            "time": "10:20",
            "duration_minutes": 30,
            "client_name": "Postojeci klijent",
        }
        cursor = object()
        with patch.object(salon_app, "query_rows", return_value=[existing]) as query_rows:
            conflict = salon_app.appointment_conflict(
                salon_id=1,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                cursor=cursor,
            )
        self.assertEqual(conflict["id"], 44)
        sql = query_rows.call_args.args[0]
        self.assertIn("a.status IN ('pending', 'scheduled')", sql)
        self.assertIn("FOR UPDATE OF a", sql)

    def test_touching_appointments_do_not_conflict(self):
        existing = {
            "id": 45,
            "time": "10:30",
            "duration_minutes": 30,
            "client_name": "Sledeci klijent",
        }
        with patch.object(salon_app, "query_rows", return_value=[existing]):
            conflict = salon_app.appointment_conflict(
                salon_id=1,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
            )
        self.assertIsNone(conflict)

    def test_persist_appointment_locks_worker_before_final_availability_check(self):
        connection = QueueConnection(
            [
                {"id": 2, "salon_id": 1, "active": True},
                {"id": 99},
            ]
        )
        assignment = {
            "worker_id": 2,
            "service_id": 3,
            "assignment_active": True,
        }
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "worker_service_assignment", return_value=assignment),
            patch.object(salon_app, "appointment_availability_error", return_value=None) as availability,
        ):
            appointment_id, error = salon_app.persist_appointment_locked(
                self.salon,
                client_id=10,
                service_id=3,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                price=1500,
                status="scheduled",
                source="public",
                notes="",
                public_request=True,
                public_token="public-token",
            )
        self.assertEqual(appointment_id, 99)
        self.assertIsNone(error)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        first_sql = connection.cursor_instance.statements[0][0]
        self.assertIn("FROM workers", first_sql)
        self.assertIn("FOR UPDATE", first_sql)
        availability.assert_called_once()

    def test_persist_appointment_rolls_back_when_final_check_finds_conflict(self):
        connection = QueueConnection([{"id": 2, "salon_id": 1, "active": True}])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "worker_service_assignment", return_value={"worker_id": 2}),
            patch.object(
                salon_app,
                "appointment_availability_error",
                return_value="Radnik vec ima aktivan termin.",
            ),
        ):
            appointment_id, error = salon_app.persist_appointment_locked(
                self.salon,
                client_id=10,
                service_id=3,
                worker_id=2,
                appointment_date=self.future_date,
                appointment_time="10:00",
                duration_minutes=30,
                price=1500,
                status="scheduled",
                source="public",
                notes="",
            )
        self.assertIsNone(appointment_id)
        self.assertIn("aktivan termin", error)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(
            any(
                statement.startswith("INSERT INTO appointments")
                for statement, _ in connection.cursor_instance.statements
            )
        )


if __name__ == "__main__":
    unittest.main()
