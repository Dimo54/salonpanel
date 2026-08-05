import hashlib
import hmac
import json
import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class PaddleIntegrationTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()
        self.user = {
            "id": 7,
            "salon_id": 3,
            "role": "owner",
            "name": "Owner",
            "email": "owner@example.com",
            "email_verified": True,
        }
        self.salon = {
            "id": 3,
            "name": "Test salon",
            "slug": "test-salon",
            "business_type": "Salon",
            "subscription_status": "trial",
            "subscription_plan": "trial",
            "trial_ends_at": date.today() + timedelta(days=7),
        }

    def test_subscription_page_exposes_only_safe_checkout_configuration(self):
        with (
            patch.object(salon_app, "validated_session_user", return_value=self.user),
            patch.object(salon_app, "current_user", return_value=self.user),
            patch.object(salon_app, "current_salon", return_value=self.salon),
            patch.object(salon_app, "db_query", return_value={"id": self.salon["id"]}),
            patch.object(salon_app, "salon_settings", return_value=salon_app.DEFAULT_SALON_SETTINGS),
            patch.object(salon_app, "PADDLE_ENVIRONMENT", "sandbox"),
            patch.object(salon_app, "PADDLE_CLIENT_TOKEN", "test_client_token"),
            patch.object(salon_app, "PADDLE_API_KEY", "server_secret_api_key"),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
            patch.object(salon_app, "PADDLE_YEARLY_PRICE_ID", "pri_yearly"),
        ):
            response = self.client.get("/subscription")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-client-token="test_client_token"', body)
        self.assertIn('data-monthly-price-id="pri_monthly"', body)
        self.assertIn('data-yearly-price-id="pri_yearly"', body)
        self.assertNotIn("server_secret_api_key", body)
        self.assertIn("https://cdn.paddle.com/paddle/v2/paddle.js", body)
        self.assertIn("https://*.paddle.com", response.headers["Content-Security-Policy"])

    def test_subscription_page_keeps_trial_access_when_checkout_is_unconfigured(self):
        with (
            patch.object(salon_app, "validated_session_user", return_value=self.user),
            patch.object(salon_app, "current_user", return_value=self.user),
            patch.object(salon_app, "current_salon", return_value=self.salon),
            patch.object(salon_app, "db_query", return_value={"id": self.salon["id"]}),
            patch.object(salon_app, "salon_settings", return_value=salon_app.DEFAULT_SALON_SETTINGS),
            patch.object(salon_app, "PADDLE_CLIENT_TOKEN", ""),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", ""),
            patch.object(salon_app, "PADDLE_YEARLY_PRICE_ID", ""),
        ):
            response = self.client.get("/subscription")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Online plaćanje trenutno nije dostupno", body)
        self.assertNotIn("https://cdn.paddle.com/paddle/v2/paddle.js", body)
        self.assertIn("Probni period", body)

    def test_signed_webhook_is_recorded_without_changing_subscription(self):
        payload = {"event_id": "evt_123", "event_type": "subscription.created", "data": {}}
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = 1_900_000_000
        secret = "notification-secret"
        signature = hmac.new(
            secret.encode("utf-8"),
            str(timestamp).encode("utf-8") + b":" + raw_body,
            hashlib.sha256,
        ).hexdigest()

        with (
            patch.object(salon_app, "PADDLE_API_KEY", ""),
            patch.object(salon_app, "PADDLE_WEBHOOK_SECRET", secret),
            patch.object(salon_app, "PADDLE_WEBHOOK_TOLERANCE_SECONDS", 5),
            patch.object(salon_app.time_module, "time", return_value=timestamp),
            patch.object(salon_app, "db_execute") as db_execute,
        ):
            response = self.client.post(
                "/webhooks/paddle",
                data=raw_body,
                content_type="application/json",
                headers={"Paddle-Signature": f"ts={timestamp};h1={signature}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        db_execute.assert_called_once()
        self.assertIn("INSERT INTO subscription_events", db_execute.call_args.args[0])
        self.assertIn("ON CONFLICT (provider, paddle_event_id)", db_execute.call_args.args[0])
        self.assertEqual(db_execute.call_args.args[1][0], "evt_123")
        self.assertEqual(db_execute.call_args.args[1][1], "subscription.created")

    def test_webhook_rejects_invalid_signature_before_database_write(self):
        with (
            patch.object(salon_app, "PADDLE_WEBHOOK_SECRET", "notification-secret"),
            patch.object(salon_app, "db_execute") as db_execute,
        ):
            response = self.client.post(
                "/webhooks/paddle",
                data=b'{"event_type":"subscription.created"}',
                content_type="application/json",
                headers={"Paddle-Signature": "ts=1;h1=invalid"},
            )

        self.assertEqual(response.status_code, 401)
        db_execute.assert_not_called()

    def test_webhook_timestamp_tolerance_uses_unix_time(self):
        raw_body = b'{"event_id":"evt_old","event_type":"subscription.created"}'
        timestamp = 1_900_000_000
        secret = "notification-secret"
        signature = hmac.new(
            secret.encode("utf-8"),
            str(timestamp).encode("utf-8") + b":" + raw_body,
            hashlib.sha256,
        ).hexdigest()

        with (
            patch.object(salon_app, "PADDLE_WEBHOOK_SECRET", secret),
            patch.object(salon_app, "PADDLE_WEBHOOK_TOLERANCE_SECONDS", 5),
            patch.object(salon_app.time_module, "time", return_value=timestamp + 6) as unix_time,
            patch.object(salon_app, "db_execute") as db_execute,
        ):
            response = self.client.post(
                "/webhooks/paddle",
                data=raw_body,
                content_type="application/json",
                headers={"Paddle-Signature": f"ts={timestamp};h1={signature}"},
            )

        self.assertEqual(response.status_code, 401)
        unix_time.assert_called_once_with()
        db_execute.assert_not_called()

    def test_paddle_event_id_migration_is_additive_and_unique(self):
        migration = Path("migrations/20260805_add_paddle_event_id.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("ADD COLUMN IF NOT EXISTS paddle_event_id TEXT", migration)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS", migration)
        self.assertIn(
            "ON subscription_events(provider, paddle_event_id)",
            migration,
        )
        self.assertIn("WHERE paddle_event_id IS NOT NULL", migration)
        self.assertNotRegex(migration.upper(), r"\bUPDATE\b|\bDELETE\b")

    def test_fresh_schema_contains_paddle_event_id_uniqueness(self):
        source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn("paddle_event_id TEXT", source)
        self.assertIn(
            "ON subscription_events(provider, paddle_event_id)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
