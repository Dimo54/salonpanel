import os
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch
from xml.etree import ElementTree


os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class SeoFoundationTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()

    def test_robots_is_public_plain_text_and_references_production_sitemap(self):
        response = self.client.get("/robots.txt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/plain")
        self.assertTrue(response.content_type.startswith("text/plain"))
        body = response.get_data(as_text=True)
        self.assertIn("Sitemap: https://salonpanel.app/sitemap.xml", body)
        self.assertIn("Allow: /static/", body)
        for private_prefix in (
            "/login",
            "/dashboard",
            "/appointments",
            "/settings",
            "/subscription",
            "/super-admin",
            "/api/",
            "/s/",
        ):
            self.assertIn(f"Disallow: {private_prefix}", body)

    def test_sitemap_is_public_parseable_xml_with_only_allowed_canonical_urls(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/xml")
        root = ElementTree.fromstring(response.data)
        self.assertEqual(root.tag, "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset")
        locations = [
            element.text
            for element in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
                                        "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        ]
        self.assertEqual(
            locations,
            ["https://salonpanel.app/"],
        )
        self.assertEqual(len(locations), len(set(locations)))
        for location in locations:
            parsed = urlsplit(location)
            self.assertEqual((parsed.scheme, parsed.netloc), ("https", "salonpanel.app"))
            self.assertFalse(parsed.query)
            self.assertFalse(parsed.fragment)

        serialized = response.get_data(as_text=True)
        for private_fragment in (
            "/login",
            "/dashboard",
            "/appointments",
            "/admin",
            "/super-admin",
            "/settings",
            "/subscription",
            "/s/",
            "/api/",
            "/privacy",
            "/terms",
        ):
            self.assertNotIn(private_fragment, serialized)

    def test_anonymous_root_is_indexable_public_landing_page(self):
        response = self.client.get("/", follow_redirects=False)
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response.headers)
        self.assertNotIn("X-Robots-Tag", response.headers)
        self.assertIn('<meta name="robots" content="index, follow">', body)
        self.assertIn(
            "<title>SalonPanel | Online zakazivanje i upravljanje salonima</title>",
            body,
        )
        self.assertIn('<meta name="description" content="', body)
        self.assertIn('<link rel="canonical" href="https://salonpanel.app/">', body)
        self.assertIn('<meta property="og:title" content="', body)
        self.assertIn('<meta property="og:description" content="', body)
        self.assertIn('<meta property="og:type" content="website">', body)
        self.assertIn('<meta property="og:url" content="https://salonpanel.app/">', body)
        self.assertIn('<meta property="og:site_name" content="SalonPanel">', body)
        self.assertIn('<meta name="twitter:card" content="summary">', body)
        self.assertIn('<meta name="twitter:title" content="', body)
        self.assertIn('<meta name="twitter:description" content="', body)
        self.assertNotIn('property="og:image"', body)
        self.assertIn('href="/login"', body)
        self.assertIn('href="/register"', body)
        self.assertNotIn("/s/test", body)

    def test_authenticated_root_keeps_existing_dashboard_redirects(self):
        owner = {"id": 7, "salon_id": 3, "role": "owner", "active": True}
        with patch.object(salon_app, "validated_session_user", return_value=owner):
            response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

        super_admin = {"id": 1, "salon_id": None, "role": "super_admin", "active": True}
        with patch.object(salon_app, "validated_session_user", return_value=super_admin):
            response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/super-admin")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

        with self.client.session_transaction() as session:
            session["impersonate_salon_id"] = 3
        with patch.object(salon_app, "validated_session_user", return_value=super_admin):
            response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

    def test_seo_routes_do_not_redirect_for_an_unauthenticated_or_authenticated_session(self):
        for path in ("/robots.txt", "/sitemap.xml"):
            with self.subTest(path=path, session="anonymous"):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Location", response.headers)

        with self.client.session_transaction() as session:
            session["user_id"] = 12345
        for path in ("/robots.txt", "/sitemap.xml"):
            with self.subTest(path=path, session="present"):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Location", response.headers)

    def test_private_and_operational_pages_are_noindex(self):
        login_response = self.client.get("/login")
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(
            '<meta name="robots" content="noindex, nofollow">',
            login_response.get_data(as_text=True),
        )
        self.assertEqual(login_response.headers["X-Robots-Tag"], "noindex, nofollow")

        dashboard_response = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertEqual(dashboard_response.headers["Location"], "/login?next=/dashboard")
        self.assertEqual(dashboard_response.headers["X-Robots-Tag"], "noindex, nofollow")

        not_found_response = self.client.get("/page-that-does-not-exist")
        self.assertEqual(not_found_response.status_code, 404)
        self.assertEqual(not_found_response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(
            '<meta name="robots" content="noindex, nofollow">',
            not_found_response.get_data(as_text=True),
        )

    def test_tenant_booking_page_is_explicitly_noindex(self):
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
                "business_name": salon["name"],
                "booking_min_notice_minutes": 120,
                "booking_max_days": 90,
                "cancellation_notice_hours": 24,
            },
            "salon": salon,
            "form_data": {},
        }
        with (
            patch.object(salon_app, "db_query", return_value=salon),
            patch.object(salon_app, "public_booking_context", return_value=booking_context),
        ):
            response = self.client.get("/s/test-salon/zakazi")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(
            '<meta name="robots" content="noindex, nofollow">',
            response.get_data(as_text=True),
        )

    def test_draft_legal_pages_are_noindex_but_links_may_be_followed(self):
        for path in ("/privacy", "/terms"):
            with self.subTest(path=path):
                response = self.client.get(path)
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, follow")
                self.assertIn('<meta name="robots" content="noindex, follow">', body)
                self.assertNotIn('<link rel="canonical"', body)

    def test_new_routes_preserve_existing_security_headers(self):
        for path in ("/", "/robots.txt", "/sitemap.xml"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
