import hashlib
import hmac
import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def signed_post(self, payload, **patches):
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = 1_900_000_000
        secret = "notification-secret"
        signature = hmac.new(
            secret.encode("utf-8"),
            str(timestamp).encode("utf-8") + b":" + raw_body,
            hashlib.sha256,
        ).hexdigest()
        defaults = {
            "PADDLE_WEBHOOK_SECRET": secret,
            "PADDLE_WEBHOOK_TOLERANCE_SECONDS": 5,
        }
        defaults.update(patches)
        active_patches = [patch.object(salon_app, name, value) for name, value in defaults.items()]
        with patch.object(salon_app.time_module, "time", return_value=timestamp):
            for active_patch in active_patches:
                active_patch.start()
            try:
                return self.client.post(
                    "/webhooks/paddle",
                    data=raw_body,
                    content_type="application/json",
                    headers={"Paddle-Signature": f"ts={timestamp};h1={signature}"},
                )
            finally:
                for active_patch in reversed(active_patches):
                    active_patch.stop()

    def connection(self, fetches):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.side_effect = fetches
        connection.cursor.return_value = cursor
        return connection, cursor

    def subscription(self, **changes):
        value = {
            "id": "sub_123",
            "customer_id": "ctm_123",
            "status": "active",
            "updated_at": "2026-08-05T12:30:00Z",
            "custom_data": {"salon_id": "3"},
            "items": [{"price": {"id": "pri_monthly"}}],
        }
        value.update(changes)
        return value

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

    def test_active_subscription_page_hides_ids_and_duplicate_checkout(self):
        salon = {
            **self.salon,
            "subscription_status": "active",
            "subscription_plan": "monthly",
            "paddle_customer_id": "ctm_sensitive_customer",
            "paddle_subscription_id": "sub_sensitive_subscription",
        }
        with (
            patch.object(salon_app, "validated_session_user", return_value=self.user),
            patch.object(salon_app, "current_user", return_value=self.user),
            patch.object(salon_app, "current_salon", return_value=salon),
            patch.object(salon_app, "db_query", return_value={"id": salon["id"]}),
            patch.object(salon_app, "salon_settings", return_value=salon_app.DEFAULT_SALON_SETTINGS),
            patch.object(salon_app, "PADDLE_API_KEY", "server_secret"),
        ):
            response = self.client.get("/subscription")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Pretplata je aktivna", body)
        self.assertIn("Otvori Paddle portal", body)
        self.assertNotIn("Izaberi mesečni plan", body)
        self.assertNotIn("ctm_sensitive_customer", body)
        self.assertNotIn("sub_sensitive_subscription", body)
        self.assertNotIn("server_secret", body)

    def test_portal_button_only_appears_with_customer_id(self):
        salon = {**self.salon, "subscription_status": "blocked", "paddle_customer_id": None}
        with (
            patch.object(salon_app, "validated_session_user", return_value=self.user),
            patch.object(salon_app, "current_user", return_value=self.user),
            patch.object(salon_app, "current_salon", return_value=salon),
            patch.object(salon_app, "db_query", return_value={"id": salon["id"]}),
            patch.object(salon_app, "salon_settings", return_value=salon_app.DEFAULT_SALON_SETTINGS),
        ):
            response = self.client.get("/subscription")
        self.assertNotIn("Otvori Paddle portal", response.get_data(as_text=True))

    def test_valid_subscription_event_fetches_authoritative_data_and_updates_only_salon_id(self):
        connection, cursor = self.connection([{"id": 91}, {"id": 3}, {"id": 3}])
        authoritative = self.subscription(custom_data={"salon_id": "3", "email": "private@example.com"})
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=authoritative) as fetch,
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
        ):
            response = self.signed_post({
                "event_id": "evt_123",
                "event_type": "subscription.created",
                "data": {"id": "sub_123", "custom_data": {"salon_id": 999}},
            })

        self.assertEqual(response.status_code, 200)
        fetch.assert_called_once_with("sub_123")
        update_call = next(call for call in cursor.execute.call_args_list if "UPDATE salons" in call.args[0])
        self.assertEqual(update_call.args[1][0:4], ("active", "monthly", "ctm_123", "sub_123"))
        self.assertEqual(update_call.args[1][6], 3)
        self.assertNotIn("private@example.com", str(cursor.execute.call_args_list))
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()

    def test_duplicate_event_is_idempotent_without_api_or_salon_update(self):
        connection, cursor = self.connection([None])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription") as fetch,
        ):
            response = self.signed_post({
                "event_id": "evt_duplicate", "event_type": "subscription.updated",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["duplicate"])
        fetch.assert_not_called()
        self.assertFalse(any("UPDATE salons" in call.args[0] for call in cursor.execute.call_args_list))
        connection.commit.assert_called_once()

    def test_missing_or_malformed_authoritative_salon_id_rolls_back_without_update(self):
        for salon_id in (None, 0, -1, True, " 3", "3.0"):
            with self.subTest(salon_id=salon_id):
                connection, cursor = self.connection([{"id": 91}])
                custom_data = {} if salon_id is None else {"salon_id": salon_id}
                with (
                    patch.object(salon_app, "get_db", return_value=connection),
                    patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription(custom_data=custom_data)),
                ):
                    response = self.signed_post({
                        "event_id": f"evt_bad_{salon_id}", "event_type": "subscription.updated",
                        "data": {"id": "sub_123"},
                    })
                self.assertEqual(response.status_code, 400)
                self.assertFalse(any("UPDATE salons" in call.args[0] for call in cursor.execute.call_args_list))
                connection.rollback.assert_called_once()

    def test_unknown_salon_rolls_back_without_update(self):
        connection, cursor = self.connection([{"id": 91}, None])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription()),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
        ):
            response = self.signed_post({
                "event_id": "evt_unknown", "event_type": "subscription.updated",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(any("UPDATE salons" in call.args[0] for call in cursor.execute.call_args_list))
        connection.rollback.assert_called_once()

    def test_paddle_api_failure_rolls_back_and_returns_retryable_error(self):
        connection, cursor = self.connection([{"id": 91}])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", side_effect=salon_app.PaddleProcessingError("unavailable", 502)),
        ):
            response = self.signed_post({
                "event_id": "evt_api_failure", "event_type": "subscription.updated",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 502)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        self.assertFalse(any("UPDATE salons" in call.args[0] for call in cursor.execute.call_args_list))

    def test_unsupported_event_is_recorded_with_minimal_payload_and_ignored(self):
        connection, cursor = self.connection([{"id": 91}])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription") as fetch,
        ):
            response = self.signed_post({
                "event_id": "evt_transaction", "event_type": "transaction.completed",
                "data": {"id": "txn_123", "customer_email": "private@example.com"},
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ignored"])
        fetch.assert_not_called()
        insert_payload = cursor.execute.call_args_list[0].args[1][2]
        self.assertNotIn("private@example.com", insert_payload)
        connection.commit.assert_called_once()

    def test_stale_delivery_uses_timestamp_guard(self):
        connection, cursor = self.connection([{"id": 91}, {"id": 3}, None])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription(status="canceled")),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
        ):
            response = self.signed_post({
                "event_id": "evt_stale", "event_type": "subscription.canceled",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 200)
        update_call = next(call for call in cursor.execute.call_args_list if "UPDATE salons" in call.args[0])
        self.assertIn("paddle_subscription_updated_at <= %s", update_call.args[0])
        connection.commit.assert_called_once()

    def test_first_past_due_transition_sets_persisted_grace_deadline(self):
        connection, cursor = self.connection([
            {"id": 91},
            {"id": 3, "subscription_status": "active", "subscription_grace_ends_at": None},
            {"id": 3},
        ])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription(status="past_due")),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
            patch.object(salon_app, "SUBSCRIPTION_GRACE_DAYS", 7),
        ):
            response = self.signed_post({
                "event_id": "evt_past_due", "event_type": "subscription.updated",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 200)
        update = next(call for call in cursor.execute.call_args_list if "UPDATE salons" in call.args[0])
        self.assertEqual(update.args[1][5], datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc))

    def test_repeated_past_due_preserves_existing_grace_deadline(self):
        existing = datetime(2026, 8, 10, tzinfo=timezone.utc)
        connection, cursor = self.connection([
            {"id": 91},
            {"id": 3, "subscription_status": "past_due", "subscription_grace_ends_at": existing},
            {"id": 3},
        ])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription(status="past_due")),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
        ):
            response = self.signed_post({
                "event_id": "evt_past_due_again", "event_type": "subscription.updated",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 200)
        update = next(call for call in cursor.execute.call_args_list if "UPDATE salons" in call.args[0])
        self.assertIs(update.args[1][5], existing)

    def test_active_recovery_clears_grace_deadline(self):
        connection, cursor = self.connection([
            {"id": 91},
            {"id": 3, "subscription_status": "past_due", "subscription_grace_ends_at": datetime.now(timezone.utc)},
            {"id": 3},
        ])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription(status="active")),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
        ):
            response = self.signed_post({
                "event_id": "evt_recovered", "event_type": "subscription.updated",
                "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 200)
        update = next(call for call in cursor.execute.call_args_list if "UPDATE salons" in call.args[0])
        self.assertIsNone(update.args[1][5])

    def test_event_timestamp_is_deterministic_fallback_for_missing_authoritative_timestamp(self):
        connection, cursor = self.connection([
            {"id": 91},
            {"id": 3, "subscription_status": "active", "subscription_grace_ends_at": None},
            {"id": 3},
        ])
        authoritative = self.subscription(status="past_due", updated_at=None)
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=authoritative),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
            patch.object(salon_app, "SUBSCRIPTION_GRACE_DAYS", 7),
        ):
            response = self.signed_post({
                "event_id": "evt_timestamp_fallback", "event_type": "subscription.updated",
                "occurred_at": "2026-08-06T00:00:00Z", "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 200)
        update = next(call for call in cursor.execute.call_args_list if "UPDATE salons" in call.args[0])
        self.assertEqual(update.args[1][4], datetime(2026, 8, 6, tzinfo=timezone.utc))
        self.assertEqual(update.args[1][5], datetime(2026, 8, 13, tzinfo=timezone.utc))

    def test_missing_all_authoritative_timestamps_cannot_update_access(self):
        connection, cursor = self.connection([{"id": 91}])
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            patch.object(salon_app, "fetch_paddle_subscription", return_value=self.subscription(status="past_due", updated_at=None)),
            patch.object(salon_app, "PADDLE_MONTHLY_PRICE_ID", "pri_monthly"),
        ):
            response = self.signed_post({
                "event_id": "evt_no_timestamp", "event_type": "subscription.updated",
                "occurred_at": "invalid", "data": {"id": "sub_123"},
            })
        self.assertEqual(response.status_code, 502)
        self.assertFalse(any("UPDATE salons" in call.args[0] for call in cursor.execute.call_args_list))
        connection.rollback.assert_called_once()

    def test_webhook_rejects_invalid_signature_before_database_write(self):
        with (
            patch.object(salon_app, "PADDLE_WEBHOOK_SECRET", "notification-secret"),
            patch.object(salon_app, "get_db") as get_db,
            patch.object(salon_app, "fetch_paddle_subscription") as fetch,
        ):
            response = self.client.post(
                "/webhooks/paddle",
                data=b'{"event_type":"subscription.created"}',
                content_type="application/json",
                headers={"Paddle-Signature": "ts=1;h1=invalid"},
            )

        self.assertEqual(response.status_code, 401)
        get_db.assert_not_called()
        fetch.assert_not_called()

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
            patch.object(salon_app, "get_db") as get_db,
        ):
            response = self.client.post(
                "/webhooks/paddle",
                data=raw_body,
                content_type="application/json",
                headers={"Paddle-Signature": f"ts={timestamp};h1={signature}"},
            )

        self.assertEqual(response.status_code, 401)
        unix_time.assert_called_once_with()
        get_db.assert_not_called()

    def test_api_failure_logging_does_not_expose_secret_or_response_payload(self):
        secret = "pdl_secret_api_key"
        with (
            patch.object(salon_app, "PADDLE_API_KEY", secret),
            patch.object(salon_app, "PADDLE_ENVIRONMENT", "sandbox"),
            patch.object(salon_app, "urlopen", side_effect=RuntimeError("private@example.com")),
            patch.object(salon_app.app.logger, "warning") as warning,
            self.assertRaises(salon_app.PaddleProcessingError),
        ):
            salon_app.fetch_paddle_subscription("sub_123")
        logged = str(warning.call_args)
        self.assertNotIn(secret, logged)
        self.assertNotIn("private@example.com", logged)

    def test_status_mapping_preserves_existing_access_semantics(self):
        self.assertEqual(salon_app.paddle_subscription_status("active"), "active")
        self.assertEqual(salon_app.paddle_subscription_status("trialing"), "active")
        self.assertEqual(salon_app.paddle_subscription_status("past_due"), "past_due")
        self.assertEqual(salon_app.paddle_subscription_status("paused"), "blocked")
        self.assertEqual(salon_app.paddle_subscription_status("canceled"), "cancelled")
        self.assertEqual(salon_app.paddle_subscription_status("future_status"), "blocked")

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

    def test_subscription_timestamp_migration_is_additive(self):
        migration = Path("migrations/20260805_add_paddle_subscription_updated_at.sql").read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS paddle_subscription_updated_at TIMESTAMPTZ", migration)
        self.assertNotRegex(migration.upper(), r"\bUPDATE\b|\bDELETE\b")


if __name__ == "__main__":
    unittest.main()
