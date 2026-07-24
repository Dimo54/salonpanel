import hashlib
import hmac
import csv
import io
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

    def booking_context(self, salon, form_data=None):
        return {
            "services": [],
            "booking_data": {},
            "settings": {
                **salon_app.DEFAULT_SALON_SETTINGS,
                "business_name": salon["name"],
                "booking_min_notice_minutes": 120,
                "booking_max_days": 90,
                "cancellation_notice_hours": 24,
            },
            "salon": salon,
            "form_data": form_data or {},
        }

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

    def test_external_referrer_is_not_used_for_status_redirect(self):
        user = {"id": 5, "salon_id": 8, "role": "owner", "active": True}
        salon = {"id": 8, "subscription_status": "active"}
        with (
            patch.object(salon_app, "validated_session_user", return_value=user),
            patch.object(salon_app, "current_salon", return_value=salon),
            patch.object(salon_app, "subscription_is_allowed", return_value=True),
            patch.object(salon_app, "db_query", return_value={"id": salon["id"]}),
        ):
            response = self.client.post(
                "/appointments/12/status",
                data={"status": "invalid", "csrf_token": self.csrf_token()},
                headers={"Referer": "https://attacker.example/redirect"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/appointments")

    def test_csrf_error_link_does_not_use_external_referrer(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "expected-token"
        response = self.client.post(
            "/login",
            data={"csrf_token": "invalid-token"},
            headers={"Referer": "https://attacker.example/redirect"},
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("attacker.example", html)
        self.assertIn('href="/"', html)

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

    def test_csrf_rejects_unprotected_post(self):
        response = self.client.post("/login", data={"email": "test@example.com", "password": "x"})
        self.assertEqual(response.status_code, 400)

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

    def test_public_booking_form_responses_are_no_store(self):
        salon = {
            "id": 12,
            "name": "Test salon",
            "slug": "test-salon",
            "subscription_status": "active",
        }
        with (
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(
                salon_app,
                "public_booking_context",
                return_value=self.booking_context(salon),
            ),
        ):
            response = self.client.get("/s/test-salon/zakazi")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_public_booking_validation_failure_is_no_store(self):
        salon = {
            "id": 12,
            "name": "Test salon",
            "slug": "test-salon",
            "subscription_status": "active",
        }
        with (
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(salon_app, "rate_limit_exceeded", return_value=False),
            patch.object(
                salon_app,
                "public_booking_context",
                return_value=self.booking_context(salon),
            ),
        ):
            response = self.client.post(
                "/s/test-salon/zakazi",
                data={
                    "client_name": "Lični Podatak",
                    "csrf_token": self.csrf_token(),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_public_booking_conflict_is_private_and_no_store(self):
        salon = {
            "id": 12,
            "name": "Test salon",
            "slug": "test-salon",
            "subscription_status": "active",
            "booking_mode": "automatic",
        }
        generic_error = "Termin više nije dostupan. Izaberite drugo vreme."
        existing_name = "Postojeći Klijent"

        def persist_conflicting_appointment(
            selected_salon,
            client_id,
            service_id,
            worker_id,
            appointment_date,
            appointment_time,
            duration_minutes,
            price,
            status,
            source,
            notes,
            **kwargs,
        ):
            error = salon_app.appointment_availability_error(
                selected_salon,
                worker_id,
                appointment_date,
                appointment_time,
                duration_minutes,
                public_request=kwargs.get("public_request", False),
                worker={"id": worker_id, "active": True},
            )
            return None, error

        with (
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(salon_app, "rate_limit_exceeded", return_value=False),
            patch.object(
                salon_app,
                "worker_service_assignment",
                return_value={"worker_duration_minutes": 30, "worker_price": 1000},
            ),
            patch.object(salon_app, "get_or_create_client", return_value=77),
            patch.object(
                salon_app,
                "persist_appointment_locked",
                side_effect=persist_conflicting_appointment,
            ),
            patch.object(
                salon_app,
                "effective_worker_hours",
                return_value={"open": 9 * 60, "close": 17 * 60, "breaks": []},
            ),
            patch.object(salon_app, "worker_time_off_conflict", return_value=None),
            patch.object(
                salon_app,
                "appointment_conflict",
                return_value={"id": 99, "client_name": existing_name},
            ),
            patch.object(
                salon_app,
                "local_now",
                return_value=salon_app.datetime(
                    2030,
                    1,
                    9,
                    8,
                    0,
                    tzinfo=salon_app.ZoneInfo("Europe/Belgrade"),
                ),
            ),
            patch.object(
                salon_app,
                "public_booking_context",
                side_effect=lambda selected_salon, form_data=None: self.booking_context(
                    selected_salon,
                    form_data,
                ),
            ),
        ):
            response = self.client.post(
                "/s/test-salon/zakazi",
                data={
                    "client_name": "Novi Klijent",
                    "client_phone": "060000000",
                    "client_email": "new@example.com",
                    "service_id": "3",
                    "worker_id": "4",
                    "date": "2030-01-10",
                    "time": "10:00",
                    "privacy_accepted": "on",
                    "csrf_token": self.csrf_token(),
                },
            )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn(generic_error, html)
        self.assertNotIn(existing_name, html)

    def test_reminder_endpoint_requires_post_and_bearer_authorization(self):
        secret = "cron-test-secret"
        with patch.dict(salon_app.os.environ, {"CRON_SECRET": secret}):
            self.assertEqual(
                self.client.get(f"/tasks/send-reminders?secret={secret}").status_code,
                405,
            )
            self.assertEqual(
                self.client.post(f"/tasks/send-reminders?secret={secret}").status_code,
                403,
            )
            self.assertEqual(self.client.post("/tasks/send-reminders").status_code, 403)
            self.assertEqual(
                self.client.post(
                    "/tasks/send-reminders",
                    headers={"Authorization": "Bearer incorrect"},
                ).status_code,
                403,
            )

    def test_valid_reminder_authorization_reaches_existing_logic_without_email(self):
        secret = "cron-test-secret"
        with (
            patch.dict(salon_app.os.environ, {"CRON_SECRET": secret}),
            patch.object(salon_app, "db_query", return_value=[]) as db_query,
            patch.object(salon_app, "send_appointment_event") as send_event,
        ):
            response = self.client.post(
                "/tasks/send-reminders",
                headers={"Authorization": f"Bearer {secret}"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "sent": 0, "failed": 0, "checked": 0})
        db_query.assert_called_once()
        send_event.assert_not_called()

    def test_csv_safe_value_neutralizes_formula_prefixes(self):
        for prefix in ("=", "+", "-", "@"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    salon_app.csv_safe_value(f"  {prefix}formula"),
                    f"'  {prefix}formula",
                )
        self.assertEqual(salon_app.csv_safe_value("Željko Petrović"), "Željko Petrović")
        self.assertEqual(salon_app.csv_safe_value(None), "")

    def test_appointment_export_applies_csv_protection_to_editable_fields(self):
        user = {"id": 5, "salon_id": 8, "role": "owner", "active": True}
        salon = {"id": 8, "subscription_status": "active"}
        row = {
            "id": 1,
            "date": "2030-01-10",
            "time": "10:00",
            "duration_minutes": 30,
            "client": "=client",
            "phone": "+38160000000",
            "email": "=client@example.com",
            "service": "-service",
            "worker": "@worker",
            "price": 1000,
            "status": "scheduled",
            "source": "admin",
            "notes": "  =note",
        }
        with (
            patch.object(salon_app, "validated_session_user", return_value=user),
            patch.object(salon_app, "current_salon", return_value=salon),
            patch.object(salon_app, "subscription_is_allowed", return_value=True),
            patch.object(
                salon_app,
                "db_query",
                side_effect=[{"id": salon["id"]}, [row]],
            ),
        ):
            response = self.client.get("/export/appointments.csv")
        exported = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(exported[1][4], "'=client")
        self.assertEqual(exported[1][5], "'+38160000000")
        self.assertEqual(exported[1][6], "'=client@example.com")
        self.assertEqual(exported[1][7], "'-service")
        self.assertEqual(exported[1][8], "'@worker")
        self.assertEqual(exported[1][12], "'  =note")

    def test_client_export_applies_csv_protection_to_contact_fields(self):
        user = {"id": 5, "salon_id": 8, "role": "owner", "active": True}
        salon = {"id": 8, "subscription_status": "active"}
        row = {
            "id": 1,
            "name": "=client",
            "phone": "+38160000000",
            "email": "-mail@example.com",
            "notes": "@note",
            "created_at": "2030-01-10 10:00:00",
            "visits": 2,
            "revenue": 2000,
        }
        with (
            patch.object(salon_app, "validated_session_user", return_value=user),
            patch.object(salon_app, "current_salon", return_value=salon),
            patch.object(salon_app, "subscription_is_allowed", return_value=True),
            patch.object(
                salon_app,
                "db_query",
                side_effect=[{"id": salon["id"]}, [row]],
            ),
        ):
            response = self.client.get("/export/clients.csv")
        exported = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(exported[1][1], "'=client")
        self.assertEqual(exported[1][2], "'+38160000000")
        self.assertEqual(exported[1][3], "'-mail@example.com")
        self.assertEqual(exported[1][4], "'@note")

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
