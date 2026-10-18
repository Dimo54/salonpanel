import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class ReliableReminderTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True)
        self.client = salon_app.app.test_client()
        self.now = datetime(2030, 5, 10, 10, 0, tzinfo=salon_app.ZoneInfo("Europe/Belgrade"))

    def appointment(self, minutes_until, **overrides):
        start = self.now + timedelta(minutes=minutes_until)
        row = {
            "id": 9,
            "salon_id": 1,
            "date": start.date(),
            "time": start.time(),
            "status": "scheduled",
            "schedule_version": 1,
            "schedule_started_at": self.now - timedelta(days=2),
        }
        row.update(overrides)
        return row

    def test_24h_reminder_is_due_for_all_ten_minute_slots(self):
        for minute in (0, 10, 20, 30, 40, 50):
            with self.subTest(minute=minute):
                now = self.now.replace(minute=minute)
                row = self.appointment(24 * 60 - 20)
                row["date"] = (now + timedelta(minutes=24 * 60 - 20)).date()
                row["time"] = (now + timedelta(minutes=24 * 60 - 20)).time()
                row["schedule_started_at"] = now - timedelta(days=2)
                self.assertEqual(salon_app.due_reminder_type(row, now), "reminder_24h")

    def test_late_cron_still_sends_useful_24h_reminder(self):
        for late_minutes in (20, 70, 360):
            with self.subTest(late_minutes=late_minutes):
                self.assertEqual(
                    salon_app.due_reminder_type(self.appointment(24 * 60 - late_minutes), self.now),
                    "reminder_24h",
                )

    def test_two_hour_reminder_has_priority_and_never_sends_after_start(self):
        self.assertEqual(salon_app.due_reminder_type(self.appointment(120), self.now), "reminder_2h")
        self.assertEqual(salon_app.due_reminder_type(self.appointment(1), self.now), "reminder_2h")
        self.assertIsNone(salon_app.due_reminder_type(self.appointment(0), self.now))
        self.assertIsNone(salon_app.due_reminder_type(self.appointment(-1), self.now))

    def test_ineligible_statuses_do_not_receive_reminders(self):
        for status in ("cancelled", "completed", "no_show", "pending"):
            with self.subTest(status=status):
                self.assertIsNone(salon_app.due_reminder_type(self.appointment(60, status=status), self.now))

    def test_newly_scheduled_inside_24h_skips_24h_but_keeps_2h(self):
        newly_scheduled = self.appointment(23 * 60, schedule_started_at=self.now - timedelta(minutes=5))
        self.assertIsNone(salon_app.due_reminder_type(newly_scheduled, self.now))
        newly_scheduled_soon = self.appointment(90, schedule_started_at=self.now - timedelta(minutes=5))
        self.assertEqual(salon_app.due_reminder_type(newly_scheduled_soon, self.now), "reminder_2h")

    def test_claim_is_atomic_and_second_parallel_cron_cannot_claim(self):
        calls = []

        def execute(sql, params, returning=False):
            calls.append((sql, params, returning))
            if len(calls) == 1:
                return {"id": 1, "lease_token": params[4]}
            return None

        with patch.object(salon_app, "db_execute", side_effect=execute):
            first = salon_app.claim_reminder(9, 1, "reminder_2h", 1, self.now)
            second = salon_app.claim_reminder(9, 1, "reminder_2h", 1, self.now)
        self.assertTrue(first)
        self.assertIsNone(second)
        sql = calls[0][0]
        self.assertIn("ON CONFLICT (appointment_id, reminder_type, schedule_version)", sql)
        self.assertIn("lease_expires_at < %s", sql)
        self.assertIn("attempt_count < %s", sql)
        self.assertIn("updated_at < %s", sql)

    def test_failed_attempt_can_be_finished_then_retried_with_new_lease(self):
        claimed_tokens = []

        def execute(sql, params, returning=False):
            if "INSERT INTO reminder_events" in sql:
                claimed_tokens.append(params[4])
                return {"id": len(claimed_tokens), "lease_token": params[4]}
            return None

        with patch.object(salon_app, "db_execute", side_effect=execute) as db_execute:
            first = salon_app.claim_reminder(9, 1, "reminder_2h", 1, self.now)
            salon_app.finish_reminder_claim(9, "reminder_2h", 1, first, "failed", self.now, "Brevo timeout")
            second = salon_app.claim_reminder(9, 1, "reminder_2h", 1, self.now + timedelta(minutes=11))
            salon_app.finish_reminder_claim(9, "reminder_2h", 1, second, "successful", self.now + timedelta(minutes=11))
        self.assertNotEqual(first, second)
        finish_sql = [call.args[0] for call in db_execute.call_args_list if "UPDATE reminder_events" in call.args[0]][0]
        self.assertIn("lease_token = %s", finish_sql)

    def test_cron_rechecks_status_and_version_before_sending(self):
        row = self.appointment(60)
        with (
            patch.dict(os.environ, {"REMINDER_SECRET": "test-reminder-secret"}, clear=False),
            patch.object(salon_app, "local_now", return_value=self.now),
            patch.object(salon_app, "reminder_schema_ready", return_value=True),
            patch.object(salon_app, "legacy_reminder_successful", return_value=False),
            patch.object(salon_app, "db_query", side_effect=[[row], self.appointment(60, status="cancelled")]),
            patch.object(salon_app, "claim_reminder", return_value="lease"),
            patch.object(salon_app, "finish_reminder_claim") as finish,
            patch.object(salon_app, "send_appointment_message") as send,
        ):
            response = self.client.post("/tasks/send-reminders", headers={"Authorization": "Bearer test-reminder-secret"})
        self.assertEqual(response.status_code, 200)
        send.assert_not_called()
        self.assertEqual(finish.call_args.args[4], "skipped")

    def test_rescheduling_uses_database_side_version_increment_only_for_time_change(self):
        source = Path(salon_app.__file__).read_text()
        self.assertIn("schedule_version = CASE WHEN date IS DISTINCT FROM %s OR time IS DISTINCT FROM %s", source)
        self.assertIn("schedule_started_at = CASE WHEN date IS DISTINCT FROM %s OR time IS DISTINCT FROM %s", source)
        self.assertIn("THEN schedule_version + 1 ELSE schedule_version END", source)
        self.assertIn("UNIQUE (appointment_id, reminder_type, schedule_version)", source)

    def test_migration_backfills_old_successful_reminders_without_replay(self):
        migration = Path("migrations/20261015_reliable_reminders.sql").read_text()
        self.assertIn("FROM email_events e", migration)
        self.assertIn("e.successful = TRUE", migration)
        self.assertIn("'reminder-24h'", migration)
        self.assertIn("ON CONFLICT (appointment_id, reminder_type, schedule_version) DO NOTHING", migration)

    def test_legacy_success_blocks_v1_claim_but_not_rescheduled_version(self):
        with patch.object(salon_app, "db_query", return_value={"?column?": 1}) as query:
            self.assertTrue(salon_app.legacy_reminder_successful(9, "reminder_24h", 1))
            self.assertFalse(salon_app.legacy_reminder_successful(9, "reminder_24h", 2))
        query.assert_called_once()

    def test_schema_not_ready_refuses_cron_without_sending(self):
        with (
            patch.dict(os.environ, {"REMINDER_SECRET": "test-reminder-secret"}, clear=False),
            patch.object(salon_app, "reminder_schema_ready", return_value=False),
            patch.object(salon_app, "send_appointment_message") as send,
        ):
            response = self.client.post("/tasks/send-reminders", headers={"Authorization": "Bearer test-reminder-secret"})
        self.assertEqual(response.status_code, 503)
        send.assert_not_called()

    def test_schema_with_missing_reminder_unique_constraint_refuses_cron(self):
        with (
            patch.dict(os.environ, {"REMINDER_SECRET": "test-reminder-secret"}, clear=False),
            patch.object(salon_app, "db_query", return_value={"ready": False}) as query,
            patch.object(salon_app, "send_appointment_message") as send,
        ):
            self.assertFalse(salon_app.reminder_schema_ready())
            response = self.client.post("/tasks/send-reminders", headers={"Authorization": "Bearer test-reminder-secret"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("pg_get_constraintdef", query.call_args.args[0])
        # psycopg reserves '%' for DB-API placeholders; PostgreSQL LIKE
        # wildcards must therefore be escaped before this query is executed.
        self.assertIn("LIKE '%%UNIQUE (appointment_id, reminder_type, schedule_version)%%'", query.call_args.args[0])
        self.assertIn("idx_reminder_events_retry", query.call_args.args[0])
        send.assert_not_called()

    def test_cron_continues_after_one_claim_completion_error(self):
        first, second = self.appointment(60, id=1), self.appointment(60, id=2)
        finish_calls = []

        def finish(*args):
            finish_calls.append(args)
            if args[0] == 1:
                raise RuntimeError("database write failed")

        with (
            patch.dict(os.environ, {"REMINDER_SECRET": "test-reminder-secret"}, clear=False),
            patch.object(salon_app, "reminder_schema_ready", return_value=True),
            patch.object(salon_app, "local_now", return_value=self.now),
            patch.object(salon_app, "db_query", side_effect=[[first, second], first, second]),
            patch.object(salon_app, "legacy_reminder_successful", return_value=False),
            patch.object(salon_app, "claim_reminder", side_effect=["one", "two"]),
            patch.object(salon_app, "appointment_email_data", return_value={"client_email": "a@example.com"}),
            patch.object(salon_app, "send_appointment_message", return_value=(False, "timeout")) as send,
            patch.object(salon_app, "finish_reminder_claim", side_effect=finish),
        ):
            response = self.client.post("/tasks/send-reminders", headers={"Authorization": "Bearer test-reminder-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send.call_count, 2)
        self.assertGreaterEqual(len(finish_calls), 2)

    def test_pending_to_scheduled_resets_schedule_start(self):
        source = Path(salon_app.__file__).read_text()
        self.assertIn("status IS DISTINCT FROM 'scheduled' AND %s = 'scheduled'", source)

    def test_dst_spring_and_autumn_wall_clock_boundaries_remain_eligible(self):
        tz = salon_app.ZoneInfo("Europe/Belgrade")
        # Serbia changes to CEST on 2026-03-29 and back on 2026-10-25.
        for now, start in (
            (datetime(2026, 3, 28, 3, 30, tzinfo=tz), datetime(2026, 3, 29, 3, 30, tzinfo=tz)),
            (datetime(2026, 10, 24, 3, 30, tzinfo=tz), datetime(2026, 10, 25, 3, 30, tzinfo=tz)),
        ):
            with self.subTest(now=now):
                row = self.appointment(0)
                row.update(date=start.date(), time=start.time(), schedule_started_at=now - timedelta(days=2))
                self.assertEqual(salon_app.due_reminder_type(row, now), "reminder_24h")

    def test_utc_and_belgrade_instants_have_distinct_wall_clock_values(self):
        tz = salon_app.ZoneInfo("Europe/Belgrade")
        utc = salon_app.timezone.utc
        instant = datetime(2026, 3, 29, 0, 30, tzinfo=utc)
        self.assertEqual(instant.astimezone(tz).replace(tzinfo=None), datetime(2026, 3, 29, 1, 30))
        self.assertEqual(datetime(2026, 3, 29, 0, 30), instant.replace(tzinfo=None))

    def test_reminder_database_values_are_naive_belgrade_wall_clock(self):
        utc = datetime(2026, 10, 25, 0, 30, tzinfo=salon_app.timezone.utc)
        value = salon_app.reminder_wall_clock(utc)
        self.assertIsNone(value.tzinfo)
        self.assertEqual(value, datetime(2026, 10, 25, 2, 30))


if __name__ == "__main__":
    unittest.main()
