"""Optional integration coverage for the migration on an isolated PostgreSQL DB.

Set SALONPANEL_TEST_DATABASE_URL to one of the dedicated empty local databases
listed in APPROVED_TEST_DATABASES. This module never falls back to DATABASE_URL.
"""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from unittest.mock import patch

import threading

import psycopg

os.environ.setdefault("SALONPANEL_SKIP_DB_INIT", "1")
import app as salon_app


TEST_URL = os.environ.get("SALONPANEL_TEST_DATABASE_URL", "")
APPROVED_TEST_DATABASES = frozenset({
    "salonpanel_reminders_test",
    "salonpanel_reminders_final_test",
    "salonpanel_reminders_legacy_past_test",
    "salonpanel_reminders_write_freeze_test",
})
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
LOCAL_TEST_PORT = 55432


def approved_local_test_url_without_ssl(value):
    """Add sslmode=disable only after the dedicated local target is verified."""
    parsed = urlparse(value)
    if (
        parsed.hostname not in LOCAL_HOSTS
        or parsed.port != LOCAL_TEST_PORT
        or parsed.path.lstrip("/") not in APPROVED_TEST_DATABASES
    ):
        raise ValueError("integration URL must target the approved local dedicated database")
    parameters = dict(parse_qsl(parsed.query, keep_blank_values=True))
    parameters["sslmode"] = "disable"
    return urlunparse(parsed._replace(query=urlencode(parameters)))


@unittest.skipUnless(TEST_URL, "SALONPANEL_TEST_DATABASE_URL is not configured")
class ReliableReminderPostgresTests(unittest.TestCase):
    def setUp(self):
        try:
            self.local_test_url = approved_local_test_url_without_ssl(TEST_URL)
        except ValueError:
            self.fail(
                f"integration URL must target 127.0.0.1:{LOCAL_TEST_PORT} "
                f"and one of the dedicated databases {sorted(APPROVED_TEST_DATABASES)!r}"
            )
        self.test_database = urlparse(self.local_test_url).path.lstrip("/")
        self.connection = psycopg.connect(self.local_test_url)
        with self.connection.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] != self.test_database:
                self.connection.close()
                self.fail("connected database identity does not match the dedicated test database")
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """)
            existing_tables = [row[0] for row in cur.fetchall()]
            if existing_tables:
                self.connection.close()
                self.fail("refusing to modify a non-empty test database")
            cur.execute("CREATE TABLE salons (id integer PRIMARY KEY)")
            cur.execute("""
                CREATE TABLE appointments (
                    id integer PRIMARY KEY,
                    salon_id integer NOT NULL,
                    date date NOT NULL DEFAULT CURRENT_DATE - 1,
                    time time NOT NULL DEFAULT TIME '09:00',
                    status text NOT NULL DEFAULT 'scheduled',
                    created_at timestamp NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE email_events (
                    id BIGSERIAL PRIMARY KEY,
                    salon_id INTEGER REFERENCES salons(id) ON DELETE CASCADE,
                    appointment_id INTEGER REFERENCES appointments(id) ON DELETE CASCADE,
                    event_key TEXT NOT NULL,
                    recipient TEXT DEFAULT '' CHECK (recipient <> 'fail@example.test'),
                    successful BOOLEAN NOT NULL DEFAULT FALSE,
                    error_message TEXT DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (appointment_id, event_key)
                )
            """)
            cur.execute("INSERT INTO salons VALUES (1)")
            cur.execute("""
                INSERT INTO appointments (id, salon_id, date, time, status, created_at)
                VALUES (10, 1, CURRENT_DATE - 1, TIME '09:00', 'scheduled', %s)
            """, (datetime(2029, 1, 2, 8, 30),))
            cur.execute("INSERT INTO email_events (salon_id, appointment_id, event_key, successful, created_at) VALUES (NULL, 10, 'reminder-24h', TRUE, %s), (NULL, NULL, 'reminder-2h', TRUE, %s)", (datetime(2029, 1, 3), datetime(2029, 1, 3)))
        self.connection.commit()
        self.created_tables = True

    def tearDown(self):
        if getattr(self, "created_tables", False):
            with self.connection.cursor() as cur:
                cur.execute("SELECT current_database()")
                if cur.fetchone()[0] != self.test_database:
                    self.fail("refusing cleanup: database identity changed")
                # These are tables created above in an empty dedicated database;
                # no CASCADE is used and no pre-existing data is ever removed.
                cur.execute("DROP TABLE IF EXISTS reminder_events")
                cur.execute("DROP TABLE email_events")
                cur.execute("DROP TABLE appointments")
                cur.execute("DROP TABLE salons")
            self.connection.commit()
        self.connection.close()

    def apply(self, convention="local-wall-clock"):
        sql = Path("migrations/20261015_reliable_reminders.sql").read_text(encoding="utf-8")
        with self.connection.cursor() as cur:
            cur.execute("SET TIME ZONE 'Europe/Belgrade'")
            cur.execute(
                "SELECT set_config('salonpanel.history_timestamp_convention', %s, false)",
                (convention,),
            )
            cur.execute(sql)
        self.connection.commit()

    def test_backfill_preserves_history_and_is_idempotent(self):
        self.apply()
        self.apply()
        with self.connection.cursor() as cur:
            cur.execute("SELECT schedule_started_at, schedule_version FROM appointments WHERE id = 10")
            self.assertEqual(cur.fetchone(), (datetime(2029, 1, 2, 8, 30), 1))
            cur.execute("SELECT salon_id, appointment_id, reminder_type, state FROM reminder_events")
            self.assertEqual(cur.fetchall(), [(1, 10, "reminder_24h", "successful")])

    def test_utc_history_is_converted_to_belgrade_wall_clock(self):
        with self.connection.cursor() as cur:
            cur.execute("SET TIME ZONE 'Europe/Belgrade'")
            cur.execute("SET salonpanel.history_timestamp_convention TO 'utc'")
            cur.execute(Path("migrations/20261015_reliable_reminders.sql").read_text(encoding="utf-8"))
        self.connection.commit()
        with self.connection.cursor() as cur:
            cur.execute("SELECT schedule_started_at FROM appointments WHERE id = 10")
            self.assertEqual(cur.fetchone()[0], datetime(2029, 1, 2, 9, 30))

    def test_legacy_past_only_backfills_neutral_anchor_and_legacy_success_idempotently(self):
        self.apply("legacy-past-only")
        self.apply("legacy-past-only")
        with self.connection.cursor() as cur:
            cur.execute("SELECT date, schedule_started_at, schedule_version FROM appointments WHERE id = 10")
            appointment_date, schedule_started_at, schedule_version = cur.fetchone()
            self.assertEqual(schedule_started_at, datetime.combine(appointment_date, time.min))
            self.assertEqual(schedule_version, 1)
            cur.execute("SELECT reminder_type, state, count(*) FROM reminder_events GROUP BY reminder_type, state")
            self.assertEqual(cur.fetchall(), [("reminder_24h", "successful", 1)])

        # A neutral anchor for a past appointment cannot make it reminder due.
        row = {
            "date": appointment_date,
            "time": time(9),
            "status": "scheduled",
            "schedule_started_at": schedule_started_at,
        }
        now = datetime.combine(appointment_date + timedelta(days=1), time(10), salon_app.ZoneInfo("Europe/Belgrade"))
        self.assertIsNone(salon_app.due_reminder_type(row, now))

    def test_legacy_past_only_rejects_today_or_future_appointments(self):
        for offset in (0, 1):
            with self.subTest(offset=offset):
                with self.connection.cursor() as cur:
                    cur.execute("UPDATE appointments SET date = CURRENT_DATE + %s WHERE id = 10", (offset,))
                self.connection.commit()
                with self.assertRaises(psycopg.Error):
                    self.apply("legacy-past-only")
                self.connection.rollback()

    def test_legacy_past_only_historical_reschedule_gets_new_local_schedule_version(self):
        self.apply("legacy-past-only")
        rescheduled_at = datetime(2030, 5, 10, 11, 30)
        new_date = rescheduled_at.date()
        new_time = rescheduled_at.time()
        with self.connection.cursor() as cur:
            # This is what the freeze-aware deployed application does after
            # the operator turns the public write freeze off.
            cur.execute(
                "SELECT set_config('salonpanel.appointment_write_freeze_bypass', 'on', false)"
            )
            # This is the production schedule-version transition expressed
            # against a real migrated PostgreSQL row.
            cur.execute("""
                UPDATE appointments
                SET date = %s, time = %s,
                    schedule_version = CASE WHEN date IS DISTINCT FROM %s OR time IS DISTINCT FROM %s
                        THEN schedule_version + 1 ELSE schedule_version END,
                    schedule_started_at = CASE WHEN date IS DISTINCT FROM %s OR time IS DISTINCT FROM %s
                        THEN %s ELSE schedule_started_at END
                WHERE id = 10
            """, (new_date, new_time, new_date, new_time, new_date, new_time, rescheduled_at))
        self.connection.commit()
        # A completed migration remains idempotent after this now-future row
        # exists; the past-only gate applies only while a backfill is needed.
        self.apply("legacy-past-only")
        with self.connection.cursor() as cur:
            cur.execute("SELECT schedule_version, schedule_started_at FROM appointments WHERE id = 10")
            self.assertEqual(cur.fetchone(), (2, rescheduled_at))

    def test_legacy_past_only_database_guard_blocks_old_code_and_allows_new_code(self):
        """A raw old-client connection cannot write during a Render overlap."""
        self.apply("legacy-past-only")
        with psycopg.connect(self.local_test_url) as old_client:
            with old_client.cursor() as cur:
                with self.assertRaises(psycopg.Error) as blocked:
                    cur.execute(
                        "INSERT INTO appointments (id, salon_id, created_at) "
                        "VALUES (11, 1, CURRENT_TIMESTAMP)"
                    )
            self.assertEqual(blocked.exception.sqlstate, "55000")
            old_client.rollback()

        with psycopg.connect(self.local_test_url) as freeze_aware_client:
            with freeze_aware_client.cursor() as cur:
                cur.execute(
                    "SELECT set_config('salonpanel.appointment_write_freeze_bypass', 'on', false)"
                )
                cur.execute(
                    "INSERT INTO appointments (id, salon_id, created_at) "
                    "VALUES (11, 1, CURRENT_TIMESTAMP)"
                )
            freeze_aware_client.commit()
        with self.connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM appointments WHERE id = 11")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_new_application_connection_keeps_database_guard_while_freeze_is_on(self):
        self.apply("legacy-past-only")
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": self.local_test_url,
                "SALONPANEL_APPOINTMENT_WRITE_FREEZE": "1",
            },
            clear=False,
        ):
            with salon_app.app.app_context():
                connection = salon_app.get_db()
                with connection.cursor() as cur:
                    with self.assertRaises(psycopg.Error) as blocked:
                        cur.execute(
                            "INSERT INTO appointments (id, salon_id, created_at) "
                            "VALUES (11, 1, CURRENT_TIMESTAMP)"
                        )
                    self.assertEqual(blocked.exception.sqlstate, "55000")
                connection.rollback()

    def test_legacy_past_only_database_guard_rejects_parallel_old_writers(self):
        """No overlapping old process can slip an appointment through the guard."""
        self.apply("legacy-past-only")
        start = threading.Barrier(2, timeout=5)

        def old_write(appointment_id):
            with psycopg.connect(self.local_test_url) as connection:
                with connection.cursor() as cur:
                    cur.execute("SET statement_timeout = '5s'")
                    start.wait(timeout=5)
                    try:
                        cur.execute(
                            "INSERT INTO appointments (id, salon_id, created_at) "
                            "VALUES (%s, 1, CURRENT_TIMESTAMP)",
                            (appointment_id,),
                        )
                    except psycopg.Error as error:
                        return error.sqlstate
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(old_write, appointment_id) for appointment_id in (11, 12)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results, ["55000", "55000"])
        with self.connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM appointments WHERE id IN (11, 12)")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_legacy_past_only_lock_blocks_concurrent_appointment_insert(self):
        """The migration's table lock rejects concurrent appointment writes."""
        self.assertIn("LOCK TABLE appointments IN SHARE ROW EXCLUSIVE MODE", Path(
            "migrations/20261015_reliable_reminders.sql"
        ).read_text(encoding="utf-8"))
        with psycopg.connect(self.local_test_url) as holder, psycopg.connect(self.local_test_url) as writer:
            with holder.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute("LOCK TABLE appointments IN SHARE ROW EXCLUSIVE MODE")
            with writer.cursor() as cur:
                cur.execute("SET lock_timeout = '200ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    cur.execute("INSERT INTO appointments (id, salon_id, created_at) VALUES (11, 1, CURRENT_TIMESTAMP)")
            writer.rollback()
            holder.rollback()

    def test_parallel_claim_allows_exactly_one_active_lease_then_reclaims_after_expiry(self):
        """Two real DB connections cannot both reserve the same reminder row."""
        self.apply()
        claimed_at = datetime(2030, 1, 2, 10, 0)
        start = threading.Barrier(2, timeout=5)

        def claim_from_separate_connection():
            with salon_app.app.app_context():
                connection = salon_app.get_db()
                with connection.cursor() as cur:
                    cur.execute("SET statement_timeout = '5s'")
                    cur.execute("SELECT pg_backend_pid() AS pid")
                    backend_pid = cur.fetchone()["pid"]
                start.wait(timeout=5)
                token = salon_app.claim_reminder(10, 1, "reminder_2h", 1, claimed_at)
                return backend_pid, token

        # The app function under test obtains one request-scoped connection per
        # worker. The executor/future timeouts make lock regressions fail fast.
        # database_url() preserves this explicit mode, so only the verified
        # local integration connection bypasses production TLS enforcement.
        with patch.dict(os.environ, {"DATABASE_URL": self.local_test_url}, clear=False):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(claim_from_separate_connection) for _ in range(2)]
                results = [future.result(timeout=10) for future in futures]

            backend_pids, tokens = zip(*results)
            self.assertEqual(len(set(backend_pids)), 2)
            self.assertEqual(sum(token is not None for token in tokens), 1)

            # The same event remains unavailable until its ten-minute lease has
            # expired; the production UPSERT then permits one new reservation.
            with salon_app.app.app_context():
                second_while_active = salon_app.claim_reminder(10, 1, "reminder_2h", 1, claimed_at)
                reclaimed = salon_app.claim_reminder(
                    10, 1, "reminder_2h", 1,
                    claimed_at + salon_app.timedelta(minutes=salon_app.REMINDER_LEASE_MINUTES + 1),
                )
                self.assertIsNone(second_while_active)
                self.assertIsNotNone(reclaimed)

    def test_successful_claim_dual_writes_exactly_one_new_and_legacy_event(self):
        self.apply()
        now = datetime(2030, 1, 2, 10, 0)
        with patch.dict(os.environ, {"DATABASE_URL": self.local_test_url}, clear=False):
            with salon_app.app.app_context():
                token = salon_app.claim_reminder(10, 1, "reminder_2h", 1, now)
                self.assertIsNotNone(token)
                salon_app.finish_successful_reminder_claim(
                    10, 1, "reminder_2h", 1, token, "client@example.test", now,
                )
                self.assertIsNone(salon_app.claim_reminder(10, 1, "reminder_2h", 1, now))
        with self.connection.cursor() as cur:
            cur.execute("SELECT state, count(*) FROM reminder_events WHERE appointment_id = 10 AND reminder_type = 'reminder_2h' GROUP BY state")
            self.assertEqual(cur.fetchall(), [("successful", 1)])
            cur.execute("SELECT event_key, successful, count(*) FROM email_events WHERE appointment_id = 10 AND event_key = 'reminder-2h' GROUP BY event_key, successful")
            self.assertEqual(cur.fetchall(), [("reminder-2h", True, 1)])

    def test_dual_write_rolls_back_when_legacy_insert_fails(self):
        self.apply()
        now = datetime(2030, 1, 2, 10, 0)
        with patch.dict(os.environ, {"DATABASE_URL": self.local_test_url}, clear=False):
            with salon_app.app.app_context():
                token = salon_app.claim_reminder(10, 1, "reminder_2h", 1, now)
                with self.assertRaises(psycopg.Error):
                    salon_app.finish_successful_reminder_claim(
                        10, 1, "reminder_2h", 1, token, "fail@example.test", now,
                    )
        with self.connection.cursor() as cur:
            cur.execute("SELECT state, sent_at FROM reminder_events WHERE appointment_id = 10 AND reminder_type = 'reminder_2h'")
            self.assertEqual(cur.fetchone(), ("processing", None))
            cur.execute("SELECT count(*) FROM email_events WHERE appointment_id = 10 AND event_key = 'reminder-2h'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_failed_claim_respects_cooldown_then_retries_without_duplicate_event(self):
        self.apply()
        now = datetime(2030, 1, 2, 10, 0)
        with patch.dict(os.environ, {"DATABASE_URL": self.local_test_url}, clear=False):
            with salon_app.app.app_context():
                first = salon_app.claim_reminder(10, 1, "reminder_2h", 1, now)
                salon_app.finish_reminder_claim(10, "reminder_2h", 1, first, "failed", now, "timeout")
                self.assertIsNone(salon_app.claim_reminder(
                    10, 1, "reminder_2h", 1,
                    now + salon_app.timedelta(minutes=salon_app.REMINDER_RETRY_DELAY_MINUTES - 1),
                ))
                second = salon_app.claim_reminder(
                    10, 1, "reminder_2h", 1,
                    now + salon_app.timedelta(minutes=salon_app.REMINDER_RETRY_DELAY_MINUTES + 1),
                )
                self.assertIsNotNone(second)
        with self.connection.cursor() as cur:
            cur.execute("SELECT attempt_count, state, count(*) OVER () FROM reminder_events WHERE appointment_id = 10 AND reminder_type = 'reminder_2h'")
            self.assertEqual(cur.fetchone(), (2, "processing", 1))
