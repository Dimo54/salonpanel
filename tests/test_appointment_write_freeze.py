import os
import unittest
from datetime import date, time
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class AppointmentWriteFreezeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True)

    def test_freeze_rejects_appointment_persist_before_database_access(self):
        with (
            patch.dict(os.environ, {"SALONPANEL_APPOINTMENT_WRITE_FREEZE": "1"}, clear=False),
            patch.object(salon_app, "get_db") as get_db,
        ):
            appointment_id, error = salon_app.persist_appointment_locked(
                {"id": 1}, 2, 3, date(2030, 1, 2), time(10), 30, 1000,
                "scheduled", "admin", "",
            )
        self.assertIsNone(appointment_id)
        self.assertEqual(error, salon_app.APPOINTMENT_WRITE_FREEZE_MESSAGE)
        get_db.assert_not_called()

    def test_freeze_rejects_delete_before_cancellation_email_or_database_delete(self):
        with (
            salon_app.app.test_request_context("/appointments/9/delete", method="POST"),
            patch.dict(os.environ, {"SALONPANEL_APPOINTMENT_WRITE_FREEZE": "1"}, clear=False),
            patch.object(salon_app, "send_appointment_event") as send_event,
            patch.object(salon_app, "db_execute") as db_execute,
        ):
            response = salon_app.appointment_delete.__wrapped__(9)
        self.assertEqual(response.status_code, 302)
        send_event.assert_not_called()
        db_execute.assert_not_called()

    def test_freeze_rejects_public_booking_before_rate_limit_write(self):
        salon = {"id": 1, "name": "Test salon", "slug": "test-salon"}
        with (
            salon_app.app.test_request_context("/s/test-salon/zakazi", method="POST", data={"date": "2030-01-02"}),
            patch.dict(os.environ, {"SALONPANEL_APPOINTMENT_WRITE_FREEZE": "1"}, clear=False),
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(salon_app, "subscription_is_allowed", return_value=True),
            patch.object(salon_app, "public_booking_context", return_value={}),
            patch.object(salon_app, "rate_limit_exceeded") as rate_limit,
            patch.object(salon_app, "render_template", return_value="frozen"),
        ):
            response = salon_app.public_booking("test-salon")
        self.assertEqual(response, "frozen")
        rate_limit.assert_not_called()
