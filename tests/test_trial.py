import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class TrialRegistrationTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()

    def csrf_token(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf-token"
        return "test-csrf-token"

    def test_new_salon_receives_seven_day_trial(self):
        today = date(2026, 8, 5)
        inserted_salons = []

        def fake_db_execute(query, params=(), **kwargs):
            if "INSERT INTO salons" in query:
                inserted_salons.append(params)
                return {"id": 101}
            if "INSERT INTO users" in query:
                return {
                    "id": 202,
                    "salon_id": 101,
                    "role": "owner",
                    "name": "Test Owner",
                    "email": "owner@example.com",
                }
            return None

        with (
            patch.object(salon_app, "current_user", return_value=None),
            patch.object(salon_app, "db_query", return_value=None),
            patch.object(salon_app, "db_execute", side_effect=fake_db_execute),
            patch.object(salon_app, "unique_slug", return_value="test-salon"),
            patch.object(salon_app, "local_today", return_value=today),
            patch.object(salon_app, "create_default_services"),
            patch.object(salon_app, "ensure_salon_default_worker", return_value=303),
            patch.object(salon_app, "seed_salon_working_hours"),
            patch.object(salon_app, "seed_worker_working_hours"),
            patch.object(salon_app, "send_verification_for_user", return_value=(True, None)),
        ):
            response = self.client.post(
                "/register",
                data={
                    "salon_name": "Test Salon",
                    "owner_name": "Test Owner",
                    "email": "owner@example.com",
                    "password": "strong-password",
                    "password_confirm": "strong-password",
                    "phone": "+381601234567",
                    "business_type": "Hair Salon",
                    "privacy_accepted": "on",
                    "csrf_token": self.csrf_token(),
                },
            )

        self.assertEqual(salon_app.TRIAL_DAYS, 7)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(inserted_salons), 1)
        self.assertEqual(inserted_salons[0][-1], today + timedelta(days=7))


if __name__ == "__main__":
    unittest.main()
