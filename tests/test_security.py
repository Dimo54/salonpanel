import hashlib
import hmac
import os
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class FakeCursor:
    def __init__(self, total):
        self.total = total
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        self.statements.append((" ".join(query.split()), params))

    def fetchone(self):
        return {"total": self.total}


class FakeConnection:
    def __init__(self, total):
        self.cursor_instance = FakeCursor(total)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class SecurityTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()

    def csrf_token(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf-token"
        return "test-csrf-token"

    def render_base_for_user(self, user, salon):
        settings = {
            **salon_app.DEFAULT_SALON_SETTINGS,
            "business_name": salon["name"],
            "booking_min_notice_minutes": 120,
            "booking_max_days": 90,
            "cancellation_notice_hours": 24,
        }
        with salon_app.app.test_request_context("/"):
            salon_app.session["csrf_token"] = "rendered-csrf-token"
            salon_app.session["user_id"] = user["id"]
            if user["role"] == "super_admin":
                salon_app.session["impersonate_salon_id"] = salon["id"]
            with (
                patch.object(salon_app, "current_user", return_value=user),
                patch.object(salon_app, "current_salon", return_value=salon),
                patch.object(salon_app, "salon_settings", return_value=settings),
            ):
                return salon_app.render_template("base.html")

    def assert_form_has_rendered_csrf(self, html, action):
        match = re.search(
            rf'<form\b[^>]*action="{re.escape(action)}"[^>]*>(.*?)</form>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(match, f"Form for {action} was not rendered.")
        self.assertRegex(
            match.group(1),
            r'<input\b[^>]*name="csrf_token"[^>]*value="rendered-csrf-token"[^>]*>',
        )

    def test_safe_local_redirect_accepts_only_local_paths(self):
        self.assertEqual(
            salon_app.safe_local_redirect("/appointments?status=pending#today"),
            "/appointments?status=pending#today",
        )
        for unsafe in (
            None,
            "",
            "appointments",
            "https://example.com",
            "//example.com/path",
            "/\\example.com/path",
            "/appointments\nLocation: https://example.com",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertIsNone(salon_app.safe_local_redirect(unsafe))

    def test_login_rejects_scheme_relative_next_redirect(self):
        user = {
            "id": 7,
            "salon_id": 3,
            "role": "owner",
            "name": "Test",
            "email": "test@example.com",
            "password_hash": salon_app.generate_password_hash("correct-password"),
        }
        with (
            patch.object(salon_app, "rate_limit_exceeded", return_value=False),
            patch.object(salon_app, "login_is_rate_limited", return_value=False),
            patch.object(salon_app, "record_login_attempt"),
            patch.object(salon_app, "db_query", return_value=user),
        ):
            response = self.client.post(
                "/login?next=//example.com/steal",
                data={
                    "email": user["email"],
                    "password": "correct-password",
                    "csrf_token": self.csrf_token(),
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_preserves_valid_local_next_redirect(self):
        user = {
            "id": 8,
            "salon_id": 4,
            "role": "owner",
            "name": "Test",
            "email": "owner@example.com",
            "password_hash": salon_app.generate_password_hash("correct-password"),
        }
        with (
            patch.object(salon_app, "rate_limit_exceeded", return_value=False),
            patch.object(salon_app, "login_is_rate_limited", return_value=False),
            patch.object(salon_app, "record_login_attempt"),
            patch.object(salon_app, "db_query", return_value=user),
        ):
            response = self.client.post(
                "/login?next=/appointments?status=pending",
                data={
                    "email": user["email"],
                    "password": "correct-password",
                    "csrf_token": self.csrf_token(),
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/appointments?status=pending")

    def test_security_headers_include_restrictive_csp(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")

    def test_csrf_rejects_unauthenticated_login_post_with_missing_or_invalid_token(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "expected-csrf-token"
        for supplied_token in (None, "invalid-csrf-token"):
            with self.subTest(supplied_token=supplied_token):
                data = {"email": "test@example.com", "password": "x"}
                if supplied_token is not None:
                    data["csrf_token"] = supplied_token
                response = self.client.post("/login", data=data)
                self.assertEqual(response.status_code, 400)

    def test_authenticated_duplicate_login_post_redirects_without_authentication_work(self):
        user = {"id": 7, "salon_id": 3, "role": "owner", "active": True}
        with self.client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["user_role"] = user["role"]
            session["salon_id"] = user["salon_id"]
        with (
            patch.object(salon_app, "db_query", return_value=user) as db_query,
            patch.object(salon_app, "rate_limit_exceeded") as request_rate_limiter,
            patch.object(salon_app, "login_is_rate_limited") as login_rate_limiter,
            patch.object(salon_app, "record_login_attempt") as record_attempt,
            patch.object(salon_app, "check_password_hash") as check_password,
        ):
            response = self.client.post(
                "/login",
                data={
                    "email": "test@example.com",
                    "password": "correct-password",
                    "csrf_token": "csrf-token-from-completed-login",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        db_query.assert_called_once()
        self.assertIn("active = TRUE", db_query.call_args.args[0])
        request_rate_limiter.assert_not_called()
        login_rate_limiter.assert_not_called()
        record_attempt.assert_not_called()
        check_password.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], user["id"])

    def test_stale_or_inactive_session_does_not_bypass_login_csrf(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 999
            session["user_role"] = "owner"
            session["salon_id"] = 10
            session["csrf_token"] = "expected-csrf-token"
        with (
            patch.object(salon_app, "db_query", return_value=None) as db_query,
            patch.object(salon_app, "rate_limit_exceeded") as request_rate_limiter,
            patch.object(salon_app, "login_is_rate_limited") as login_rate_limiter,
            patch.object(salon_app, "record_login_attempt") as record_attempt,
        ):
            response = self.client.post(
                "/login",
                data={
                    "email": "test@example.com",
                    "password": "x",
                    "csrf_token": "invalid-csrf-token",
                },
            )
        self.assertEqual(response.status_code, 400)
        db_query.assert_called_once()
        self.assertIn("active = TRUE", db_query.call_args.args[0])
        request_rate_limiter.assert_not_called()
        login_rate_limiter.assert_not_called()
        record_attempt.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_rendered_login_form_opts_in_to_duplicate_submit_prevention(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.get_data(as_text=True),
            r"<form\b[^>]*\bdata-prevent-duplicate-submit(?:[=\s>])",
        )

    def test_stale_session_on_login_is_cleared_without_redirect_loop(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 999
            session["user_role"] = "owner"
            session["salon_id"] = 10
        with patch.object(salon_app, "db_query", return_value=None):
            response = self.client.get("/login", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.history), 0)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)
            self.assertNotIn("user_role", session)
            self.assertNotIn("salon_id", session)

    def test_stale_session_on_protected_route_redirects_once_and_is_cleared(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 999
            session["user_role"] = "owner"
            session["salon_id"] = 10
        with patch.object(salon_app, "db_query", return_value=None):
            response = self.client.get("/dashboard", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.history), 1)
        self.assertEqual(response.history[0].headers["Location"], "/login?next=/dashboard")
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_login_required_clears_stale_session(self):
        token = self.csrf_token()
        with self.client.session_transaction() as session:
            session["user_id"] = 999
        with patch.object(salon_app, "db_query", return_value=None):
            response = self.client.post(
                "/resend-verification",
                data={"csrf_token": token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login?next=/resend-verification")
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_super_admin_required_clears_stale_session(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 999
            session["user_role"] = "super_admin"
        with patch.object(salon_app, "db_query", return_value=None):
            response = self.client.get("/super-admin")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login?next=/super-admin")
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_missing_normal_user_salon_clears_session_and_redirects_to_login(self):
        user = {"id": 5, "salon_id": 77, "role": "owner", "active": True}
        with self.client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["user_role"] = user["role"]
            session["salon_id"] = user["salon_id"]
        with patch.object(salon_app, "db_query", side_effect=[user, None]):
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertNotEqual(response.headers["Location"], "/logout")
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_invalid_super_admin_impersonation_returns_to_super_admin(self):
        user = {"id": 1, "salon_id": None, "role": "super_admin", "active": True}
        with self.client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["user_role"] = user["role"]
            session["impersonate_salon_id"] = 404
        with patch.object(salon_app, "db_query", side_effect=[user, None]):
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/super-admin")
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], user["id"])
            self.assertEqual(session["user_role"], "super_admin")
            self.assertNotIn("impersonate_salon_id", session)

    def test_logout_is_post_only_and_clears_session(self):
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.post("/logout").status_code, 400)
        token = self.csrf_token()
        with self.client.session_transaction() as session:
            session["user_id"] = 42
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_rendered_logout_form_contains_csrf_input(self):
        user = {"id": 5, "salon_id": 8, "role": "owner", "email_verified": True}
        salon = {
            "id": 8,
            "name": "Test salon",
            "slug": "test-salon",
            "business_type": "Salon",
            "subscription_status": "active",
        }
        html = self.render_base_for_user(user, salon)
        self.assert_form_has_rendered_csrf(html, "/logout")

    def test_rendered_stop_impersonating_form_contains_csrf_input(self):
        user = {"id": 1, "salon_id": None, "role": "super_admin", "email_verified": True}
        salon = {
            "id": 12,
            "name": "Test salon",
            "slug": "test-salon",
            "business_type": "Salon",
            "subscription_status": "active",
        }
        html = self.render_base_for_user(user, salon)
        self.assert_form_has_rendered_csrf(html, "/super-admin/stop-impersonating")

    def test_stop_impersonating_is_post_only_and_csrf_protected(self):
        self.assertEqual(self.client.get("/super-admin/stop-impersonating").status_code, 405)
        self.assertEqual(self.client.post("/super-admin/stop-impersonating").status_code, 400)
        token = self.csrf_token()
        user = {"id": 1, "salon_id": None, "role": "super_admin", "active": True}
        with self.client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["user_role"] = user["role"]
            session["impersonate_salon_id"] = 12
        with patch.object(salon_app, "db_query", side_effect=[user, {"id": 12}]):
            response = self.client.post(
                "/super-admin/stop-impersonating",
                data={"csrf_token": token},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/super-admin")
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], user["id"])
            self.assertNotIn("impersonate_salon_id", session)

    def test_login_rate_limit_returns_retry_after(self):
        with patch.object(salon_app, "rate_limit_exceeded", return_value=True):
            response = self.client.post(
                "/login",
                data={
                    "email": "test@example.com",
                    "password": "wrong",
                    "csrf_token": self.csrf_token(),
                },
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], str(salon_app.LOGIN_WINDOW_MINUTES * 60))

    def test_password_reset_rate_limit_returns_retry_after_without_lookup(self):
        with (
            patch.object(salon_app, "rate_limit_exceeded", return_value=True),
            patch.object(salon_app, "db_query") as db_query,
        ):
            response = self.client.post(
                "/forgot-password",
                data={"email": "test@example.com", "csrf_token": self.csrf_token()},
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.headers["Retry-After"],
            str(salon_app.PASSWORD_RESET_WINDOW_MINUTES * 60),
        )
        db_query.assert_not_called()

    def test_public_availability_rate_limit_returns_json_429(self):
        salon = {"id": 12, "subscription_status": "active"}
        with (
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(salon_app, "rate_limit_exceeded", return_value=True),
        ):
            response = self.client.get(
                "/api/s/test-salon/availability?service_id=1&worker_id=2&date=2030-01-01"
            )
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(
            response.headers["Retry-After"],
            str(salon_app.AVAILABILITY_WINDOW_MINUTES * 60),
        )

    def test_public_booking_submission_rate_limit_returns_html_429(self):
        salon = {
            "id": 12,
            "name": "Test salon",
            "slug": "test-salon",
            "subscription_status": "active",
        }
        booking_context = {
            "services": [],
            "booking_data": {},
            "settings": {
                **salon_app.DEFAULT_SALON_SETTINGS,
                "business_name": "Test salon",
                "booking_max_days": 90,
            },
            "salon": salon,
            "form_data": {},
        }
        with (
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(salon_app, "rate_limit_exceeded", return_value=True),
            patch.object(salon_app, "public_booking_context", return_value=booking_context),
        ):
            response = self.client.post(
                "/s/test-salon/zakazi",
                data={"csrf_token": self.csrf_token()},
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.headers["Retry-After"],
            str(salon_app.BOOKING_WINDOW_MINUTES * 60),
        )

    def test_rate_limit_count_and_insert_is_serialized_and_hides_identifier(self):
        connection = FakeConnection(total=0)
        with patch.object(salon_app, "get_db", return_value=connection):
            limited = salon_app.rate_limit_exceeded("test", "private@example.com", 3, 10)
        self.assertFalse(limited)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        statements = connection.cursor_instance.statements
        self.assertTrue(any("pg_advisory_xact_lock" in statement for statement, _ in statements))
        self.assertTrue(any(statement.startswith("INSERT INTO rate_limit_attempts") for statement, _ in statements))
        serialized_params = repr([params for _, params in statements])
        self.assertNotIn("private@example.com", serialized_params)
        expected_hash = hmac.new(
            str(salon_app.app.config["SECRET_KEY"]).encode("utf-8"),
            b"salonpanel:rate-limit-identifier:v1\x00test\x00private@example.com",
            hashlib.sha256,
        ).hexdigest()
        insert_params = next(
            params
            for statement, params in statements
            if statement.startswith("INSERT INTO rate_limit_attempts")
        )
        self.assertEqual(insert_params[1], expected_hash)
        self.assertNotEqual(
            expected_hash,
            hashlib.sha256(b"private@example.com").hexdigest(),
        )

    def test_expired_rate_limit_cleanup_is_global_indexed_and_bounded(self):
        cursor = FakeCursor(total=0)
        now = datetime(2030, 1, 3, 12, 0)
        salon_app.cleanup_expired_rate_limits(cursor, now=now)
        self.assertEqual(len(cursor.statements), 1)
        statement, params = cursor.statements[0]
        self.assertIn("WHERE created_at < %s", statement)
        self.assertIn("ORDER BY created_at ASC", statement)
        self.assertIn("LIMIT %s", statement)
        self.assertNotIn("scope =", statement)
        self.assertNotIn("key_hash =", statement)
        self.assertEqual(
            params[0],
            now - timedelta(hours=salon_app.RATE_LIMIT_RETENTION_HOURS),
        )
        self.assertEqual(params[1], salon_app.RATE_LIMIT_CLEANUP_BATCH_SIZE)
        migration = Path("migrations/20260723_create_rate_limit_attempts.sql").read_text()
        self.assertIn("idx_rate_limit_attempts_created_at", migration)
        self.assertIn("ON rate_limit_attempts(created_at)", migration)

    def test_rate_limit_does_not_insert_when_limit_is_reached(self):
        connection = FakeConnection(total=3)
        with patch.object(salon_app, "get_db", return_value=connection):
            limited = salon_app.rate_limit_exceeded("test", "client-ip", 3, 10)
        self.assertTrue(limited)
        self.assertFalse(
            any(
                statement.startswith("INSERT INTO rate_limit_attempts")
                for statement, _ in connection.cursor_instance.statements
            )
        )


if __name__ == "__main__":
    unittest.main()
