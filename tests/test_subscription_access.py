import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class SubscriptionAccessDecisionTests(unittest.TestCase):
    def salon(self, status, **values):
        return {"subscription_status": status, **values}

    def test_access_statuses_and_trial_dates(self):
        today = date(2026, 8, 5)
        cases = [
            (self.salon("active"), True),
            (self.salon("trial", trial_ends_at=today + timedelta(days=1)), True),
            (self.salon("trial", trial_ends_at=today), True),
            (self.salon("trial", trial_ends_at=today - timedelta(days=1)), False),
            (self.salon("trial"), False),
            (self.salon("trial", trial_ends_at="not-a-date"), False),
            (self.salon("blocked"), False),
            (self.salon("cancelled"), False),
            (self.salon("free"), False),
            (self.salon("unknown"), False),
            (self.salon(""), False),
            (self.salon(None), False),
        ]
        for salon, expected in cases:
            with self.subTest(salon=salon):
                self.assertEqual(salon_app.salon_has_subscription_access(salon, now=today), expected)

    def test_past_due_deadline_is_inclusive_and_required(self):
        now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
        self.assertTrue(salon_app.salon_has_subscription_access(
            self.salon("past_due", subscription_grace_ends_at=now + timedelta(seconds=1)), now=now,
        ))
        self.assertTrue(salon_app.salon_has_subscription_access(
            self.salon("past_due", subscription_grace_ends_at=now), now=now,
        ))
        self.assertFalse(salon_app.salon_has_subscription_access(
            self.salon("past_due", subscription_grace_ends_at=now - timedelta(seconds=1)), now=now,
        ))
        self.assertFalse(salon_app.salon_has_subscription_access(self.salon("past_due"), now=now))
        self.assertFalse(salon_app.salon_has_subscription_access(
            self.salon("past_due", subscription_grace_ends_at="malformed"), now=now,
        ))

    def test_grace_configuration_parser(self):
        self.assertEqual(salon_app.parse_subscription_grace_days(None), 7)
        self.assertEqual(salon_app.parse_subscription_grace_days(""), 7)
        self.assertEqual(salon_app.parse_subscription_grace_days("abc"), 7)
        self.assertEqual(salon_app.parse_subscription_grace_days("-1"), 7)
        self.assertEqual(salon_app.parse_subscription_grace_days("366"), 7)
        self.assertEqual(salon_app.parse_subscription_grace_days("0"), 0)
        self.assertEqual(salon_app.parse_subscription_grace_days("14"), 14)


class SubscriptionGateTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()
        self.user = {"id": 7, "salon_id": 3, "role": "owner", "email_verified": True}
        self.salon = {
            "id": 3, "name": "Salon", "slug": "salon", "business_type": "Salon",
            "subscription_status": "active", "subscription_plan": "monthly",
            "trial_ends_at": None, "subscription_grace_ends_at": None,
        }

    def login(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 7
            session["csrf_token"] = "csrf"

    def patches(self, salon=None, user=None):
        selected_user = self.user if user is None else user
        selected_salon = self.salon if salon is None else salon
        return (
            patch.object(salon_app, "validated_session_user", return_value=selected_user),
            patch.object(salon_app, "current_user", return_value=selected_user),
            patch.object(salon_app, "current_salon", return_value=selected_salon),
            patch.object(salon_app, "db_query", return_value={"id": 3}),
        )

    def test_active_and_valid_trial_reach_protected_route(self):
        self.login()
        for salon in (
            self.salon,
            {**self.salon, "subscription_status": "trial", "trial_ends_at": date.today()},
        ):
            patches = self.patches(salon=salon)
            with self.subTest(status=salon["subscription_status"]), patches[0], patches[1], patches[2], patch.object(
                salon_app, "db_query", side_effect=[{"id": 3}, []]
            ), patch.object(salon_app, "render_template", return_value="OK"):
                response = self.client.get("/settings")
                self.assertEqual(response.status_code, 200)

    def test_denied_html_redirects_but_recovery_page_does_not_loop(self):
        self.login()
        denied = {**self.salon, "subscription_status": "trial", "trial_ends_at": date.today() - timedelta(days=1)}
        patches = self.patches(salon=denied)
        with patches[0], patches[1], patches[2], patches[3], patch.object(salon_app, "salon_settings", return_value=salon_app.DEFAULT_SALON_SETTINGS):
            response = self.client.get("/dashboard")
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/subscription"))
            page = self.client.get("/subscription")
            self.assertEqual(page.status_code, 200)

    def test_denied_json_gets_403_without_redirect(self):
        self.login()
        denied = {**self.salon, "subscription_status": "blocked"}
        patches = self.patches(salon=denied)
        with patches[0], patches[1], patches[2], patches[3]:
            response = self.client.get("/dashboard", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["ok"], False)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_super_admin_and_public_endpoints_are_exempt(self):
        self.login()
        super_user = {**self.user, "role": "super_admin", "salon_id": None}
        patches = self.patches(salon={**self.salon, "subscription_status": "blocked"}, user=super_user)
        stats = {"monthly_salons": 0, "yearly_salons": 0}
        with patches[0], patches[1], patches[2], patch.object(
            salon_app, "db_query", side_effect=[stats, [], {"total": 0}]
        ), patch.object(salon_app, "render_template", return_value="OK"):
            response = self.client.get("/super-admin")
        self.assertNotEqual(response.status_code, 302)

        with self.client.session_transaction() as session:
            session.clear()
        for path in ("/", "/login", "/register", "/forgot-password", "/privacy", "/terms", "/robots.txt", "/sitemap.xml", "/static/css/styles.css", "/webhooks/paddle"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotEqual(response.status_code, 302)
                response.close()

    def test_unauthenticated_protected_route_keeps_existing_login_redirect(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class CustomerPortalTests(unittest.TestCase):
    CUSTOMER_ID = "ctm_01jcdaf4zgm2fxw3nc0e4fn137"

    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()
        self.user = {"id": 7, "salon_id": 3, "role": "owner", "email_verified": True}
        self.salon = {
            "id": 3, "name": "Salon", "slug": "salon", "business_type": "Salon",
            "subscription_status": "blocked", "subscription_plan": "monthly",
            "trial_ends_at": None, "subscription_grace_ends_at": None,
            "paddle_customer_id": self.CUSTOMER_ID,
        }

    def login(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 7
            session["csrf_token"] = "csrf"

    def route_patches(self):
        return (
            patch.object(salon_app, "validated_session_user", return_value=self.user),
            patch.object(salon_app, "current_user", return_value=self.user),
            patch.object(salon_app, "current_salon", return_value=self.salon),
            patch.object(salon_app, "db_query", return_value={"id": 3}),
        )

    def test_route_requires_login_post_and_csrf(self):
        self.assertEqual(self.client.get("/subscription/customer-portal").status_code, 405)
        self.assertEqual(self.client.post("/subscription/customer-portal").status_code, 400)
        with self.client.session_transaction() as session:
            session["csrf_token"] = "csrf"
        response = self.client.post("/subscription/customer-portal", data={"csrf_token": "csrf"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_blocked_user_can_open_portal_using_stored_customer_only(self):
        self.login()
        patches = self.route_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            salon_app, "paddle_customer_portal_url", return_value="https://customer-portal.paddle.com/ok"
        ) as portal:
            response = self.client.post(
                "/subscription/customer-portal",
                data={"csrf_token": "csrf", "customer_id": "ctm_attacker", "redirect": "https://evil.example"},
            )
        self.assertEqual(response.status_code, 302)
        portal.assert_called_once_with(self.CUSTOMER_ID)
        self.assertEqual(response.headers["Location"], "https://customer-portal.paddle.com/ok")

    def test_missing_customer_and_api_failure_are_safe(self):
        self.login()
        self.salon["paddle_customer_id"] = None
        patches = self.route_patches()
        with patches[0], patches[1], patches[2], patches[3]:
            response = self.client.post("/subscription/customer-portal", data={"csrf_token": "csrf"})
        self.assertTrue(response.headers["Location"].endswith("/subscription"))

    def api_response(self, url):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"data": {"urls": {"general": {"overview": url}}}}).encode()
        return response

    def test_api_request_urls_headers_method_and_timeout(self):
        expected = {
            "sandbox": "https://sandbox-api.paddle.com",
            "production": "https://api.paddle.com",
        }
        for environment, base in expected.items():
            with self.subTest(environment=environment), patch.object(salon_app, "PADDLE_API_KEY", "secret"), patch.object(
                salon_app, "PADDLE_ENVIRONMENT", environment
            ), patch.object(salon_app, "urlopen", return_value=self.api_response(
                ("https://sandbox-customer-portal.paddle.com/portal" if environment == "sandbox" else "https://customer-portal.paddle.com/portal")
            )) as opened:
                salon_app.paddle_customer_portal_url(self.CUSTOMER_ID)
                request = opened.call_args.args[0]
                self.assertEqual(request.full_url, f"{base}/customers/{self.CUSTOMER_ID}/portal-sessions")
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.headers["Authorization"], "Bearer secret")
                self.assertEqual(request.headers["Paddle-version"], "1")
                self.assertEqual(opened.call_args.kwargs["timeout"], salon_app.PADDLE_API_TIMEOUT_SECONDS)

    def test_portal_redirect_url_validation(self):
        bad_urls = (
            "http://customer-portal.paddle.com/x",
            "https://evil.example/x",
            "https://customer-portal.paddle.com.evil.example/x",
            "not-a-url",
        )
        for url in bad_urls:
            with self.subTest(url=url), patch.object(salon_app, "PADDLE_API_KEY", "secret"), patch.object(
                salon_app, "PADDLE_ENVIRONMENT", "production"
            ), patch.object(salon_app, "urlopen", return_value=self.api_response(url)), self.assertRaises(salon_app.PaddleProcessingError):
                salon_app.paddle_customer_portal_url(self.CUSTOMER_ID)

    def test_invalid_response_missing_config_and_api_failure_are_generic(self):
        with patch.object(salon_app, "PADDLE_API_KEY", ""), self.assertRaises(salon_app.PaddleProcessingError):
            salon_app.paddle_customer_portal_url(self.CUSTOMER_ID)
        with patch.object(salon_app, "PADDLE_API_KEY", "secret"), patch.object(salon_app, "PADDLE_ENVIRONMENT", "production"), patch.object(
            salon_app, "urlopen", side_effect=TimeoutError("sensitive response")
        ), patch.object(salon_app.app.logger, "warning") as warning, self.assertRaises(salon_app.PaddleProcessingError):
            salon_app.paddle_customer_portal_url(self.CUSTOMER_ID)
        self.assertNotIn("secret", str(warning.call_args))
        self.assertNotIn("sensitive", str(warning.call_args))


class SubscriptionSchemaTests(unittest.TestCase):
    def test_grace_migration_and_fresh_schema_are_additive(self):
        migration = Path("migrations/20260805_add_subscription_grace_ends_at.sql").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS subscription_grace_ends_at TIMESTAMPTZ", migration)
        self.assertNotRegex(migration.upper(), r"\bUPDATE\b|\bDELETE\b|\bDROP\b")
        source = Path("app.py").read_text()
        self.assertIn("subscription_grace_ends_at TIMESTAMPTZ", source)


if __name__ == "__main__":
    unittest.main()
