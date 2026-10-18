import os
import unittest
from datetime import date, time
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class AppointmentWriteFreezeCompatibilityTests(unittest.TestCase):
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

