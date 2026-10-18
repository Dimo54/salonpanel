import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIRECTORY = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import http_smoke_test
import rollout_common
import run_paddle_migrations
import run_reminder_migrations
import validate_environment as environment_command


class MigrationToolingTests(unittest.TestCase):
    def test_required_migration_order_is_fixed(self):
        self.assertEqual(
            rollout_common.MIGRATION_FILES,
            (
                "20260805_add_paddle_event_id.sql",
                "20260805_add_paddle_subscription_updated_at.sql",
                "20260805_add_subscription_grace_ends_at.sql",
            ),
        )

    def test_missing_migration_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "migrations").mkdir()
            for name in rollout_common.MIGRATION_FILES[:-1]:
                (root / "migrations" / name).write_text("SELECT 1;", encoding="utf-8")
            self.assertEqual(
                rollout_common.missing_migrations(root),
                [rollout_common.MIGRATION_FILES[-1]],
            )

    def test_database_url_is_required_even_for_default_dry_run(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_paddle_migrations.main([]), 2)

    def test_default_mode_is_non_destructive(self):
        args = run_paddle_migrations.build_parser().parse_args([])
        self.assertFalse(args.apply)
        self.assertFalse(args.check)
        self.assertIsNone(args.environment)

    def test_database_operation_requires_explicit_environment(self):
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://example.invalid/db"}, clear=True),
            patch.object(run_paddle_migrations, "missing_migrations", return_value=[]),
            patch.object(run_paddle_migrations.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = run_paddle_migrations.main(["--apply"])
        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_production_apply_requires_explicit_confirmation(self):
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://example.invalid/db"}, clear=True),
            patch.object(run_paddle_migrations, "missing_migrations", return_value=[]),
            patch.object(run_paddle_migrations.shutil, "which", return_value="/usr/bin/psql"),
            patch.object(run_paddle_migrations.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = run_paddle_migrations.main(["--apply", "--environment", "production"])
        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_reminder_runner_keeps_database_url_out_of_psql_argv(self):
        url = "postgresql://user:private-password@localhost:5433/salonpanel_reminders_test?sslmode=require"
        environment = run_reminder_migrations.connection_environment(url, "Europe/Belgrade", "utc")
        command = run_reminder_migrations.psql_command("--command", "SELECT 1")
        self.assertNotIn(url, command)
        self.assertNotIn("private-password", " ".join(command))
        self.assertEqual(environment["PGDATABASE"], "salonpanel_reminders_test")
        self.assertEqual(environment["PGSSLMODE"], "require")
        self.assertIn("history_timestamp_convention=utc", environment["PGOPTIONS"])

    def test_reminder_runner_allows_explicit_legacy_past_only_convention(self):
        environment = run_reminder_migrations.connection_environment(
            "postgresql://user:password@127.0.0.1:55432/salonpanel_reminders_legacy_past_test?sslmode=disable",
            "Europe/Belgrade",
            "legacy-past-only",
        )
        parsed = run_reminder_migrations.build_parser().parse_args([
            "--apply", "--history-timestamp-convention", "legacy-past-only",
        ])
        self.assertEqual(parsed.history_timestamp_convention, "legacy-past-only")
        self.assertIn("history_timestamp_convention=legacy-past-only", environment["PGOPTIONS"])

    def test_reminder_apply_requires_target_and_history_confirmation_before_connection(self):
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://example.invalid/app"}, clear=True),
            patch.object(run_reminder_migrations.shutil, "which", return_value="/usr/bin/psql"),
            patch.object(run_reminder_migrations.subprocess, "run") as run,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = run_reminder_migrations.main(["--apply", "--environment", "production", "--expected-database", "app"])
        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_reminder_runner_removes_inherited_libpq_overrides(self):
        with patch.dict(os.environ, {
            "PGHOSTADDR": "203.0.113.9", "PGSERVICE": "unsafe", "PGSERVICEFILE": "/tmp/unsafe",
            "PGSSLMODE": "disable", "PGOPTIONS": "-c search_path=unsafe",
        }, clear=False):
            environment = run_reminder_migrations.connection_environment(
                "postgresql://user:password@db.example.com:5432/salon?sslmode=verify-full",
                "Europe/Belgrade",
            )
        for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["PGHOST"], "db.example.com")
        self.assertEqual(environment["PGPORT"], "5432")
        self.assertEqual(environment["PGSSLMODE"], "verify-full")
        self.assertIn("TimeZone=Europe/Belgrade", environment["PGOPTIONS"])

    def test_reminder_runner_rejects_unsafe_remote_ssl_mode(self):
        with self.assertRaises(ValueError):
            run_reminder_migrations.connection_environment(
                "postgresql://user:password@db.example.com/salon?sslmode=disable",
                "Europe/Belgrade",
            )

    def test_reminder_runner_allows_disable_only_for_local_docker_target(self):
        environment = run_reminder_migrations.connection_environment(
            "postgresql://user:password@127.0.0.1:55432/salonpanel_reminders_test?sslmode=disable",
            "Europe/Belgrade",
        )
        self.assertEqual(environment["PGSSLMODE"], "disable")

    def test_reminder_runner_rejects_wrong_target_before_connection(self):
        for expected_database, expected_host, expected_port in (
            ("salon", "db.example.com", "5432"),
            ("other", "other.example.com", "5432"),
            ("other", "db.example.com", "5433"),
        ):
            with self.subTest(database=expected_database, host=expected_host, port=expected_port):
                with (
                    patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:password@db.example.com:5432/other"}, clear=True),
                    patch.object(run_reminder_migrations.shutil, "which", return_value="/usr/bin/psql"),
                    patch.object(run_reminder_migrations.subprocess, "run") as run,
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    result = run_reminder_migrations.main([
                        "--check", "--environment", "staging", "--expected-database", expected_database,
                        "--expected-host", expected_host, "--expected-port", expected_port,
                    ])
                self.assertEqual(result, 2)
                run.assert_not_called()

    def test_reminder_target_confirmation_accepts_docker_port_forwarding(self):
        environment = run_reminder_migrations.connection_environment(
            "postgresql://user:password@127.0.0.1:55432/salonpanel_reminders_runner_backup?sslmode=disable",
            "Europe/Belgrade",
        )
        completed = SimpleNamespace(returncode=0, stdout="salonpanel_reminders_runner_backup|Europe/Belgrade\n")
        with patch.object(run_reminder_migrations, "run_psql", return_value=completed) as run:
            accepted, _ = run_reminder_migrations.target_confirmation(
                environment, "salonpanel_reminders_runner_backup", "127.0.0.1", 55432, "Europe/Belgrade",
            )
        self.assertTrue(accepted)
        command = run.call_args.args[1:]
        self.assertNotIn("inet_server_addr", " ".join(command))
        self.assertNotIn("inet_server_port", " ".join(command))

    def test_reminder_target_confirmation_rejects_wrong_timezone_or_configured_endpoint(self):
        environment = run_reminder_migrations.connection_environment(
            "postgresql://user:password@127.0.0.1:55432/salonpanel_reminders_runner_backup?sslmode=disable",
            "Europe/Belgrade",
        )
        with patch.object(
            run_reminder_migrations, "run_psql",
            return_value=SimpleNamespace(returncode=0, stdout="salonpanel_reminders_runner_backup|UTC\n"),
        ):
            accepted, _ = run_reminder_migrations.target_confirmation(
                environment, "salonpanel_reminders_runner_backup", "127.0.0.1", 55432, "Europe/Belgrade",
            )
        self.assertFalse(accepted)
        with patch.object(
            run_reminder_migrations, "run_psql",
            return_value=SimpleNamespace(returncode=0, stdout="salonpanel_reminders_runner_backup|Europe/Belgrade\n"),
        ):
            accepted, _ = run_reminder_migrations.target_confirmation(
                environment, "salonpanel_reminders_runner_backup", "127.0.0.1", 9999, "Europe/Belgrade",
            )
        self.assertFalse(accepted)


class EnvironmentValidatorTests(unittest.TestCase):
    def complete_environment(self):
        return {
            "DATABASE_URL": "postgresql://user:private-password@db.example.com/app",
            "SECRET_KEY": "secret-key-value",
            "BREVO_API_KEY": "brevo-secret-value",
            "BREVO_SENDER_EMAIL": "sender@example.com",
            "REMINDER_SECRET": "reminder-secret-value",
            "PADDLE_ENVIRONMENT": "sandbox",
            "PADDLE_CLIENT_TOKEN": "client-token-value",
            "PADDLE_API_KEY": "api-key-value",
            "PADDLE_MONTHLY_PRICE_ID": "monthly-price-value",
            "PADDLE_YEARLY_PRICE_ID": "yearly-price-value",
            "PADDLE_WEBHOOK_SECRET": "webhook-secret-value",
            "PADDLE_WEBHOOK_TOLERANCE_SECONDS": "5",
            "SUBSCRIPTION_GRACE_DAYS": "7",
        }

    def test_command_never_exposes_supplied_values(self):
        environment = self.complete_environment()
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(environment_command.main(["--mode", "sandbox"]), 0)
        rendered = output.getvalue()
        for value in environment.values():
            self.assertNotIn(value, rendered)
        self.assertIn("PADDLE_API_KEY: present", rendered)

    def test_malformed_numeric_configuration_is_rejected(self):
        environment = self.complete_environment()
        for name, value in (
            ("PADDLE_WEBHOOK_TOLERANCE_SECONDS", "-1"),
            ("PADDLE_WEBHOOK_TOLERANCE_SECONDS", "five"),
            ("SUBSCRIPTION_GRACE_DAYS", "366"),
            ("SUBSCRIPTION_GRACE_DAYS", "-1"),
        ):
            with self.subTest(name=name, value=value):
                candidate = {**environment, name: value}
                statuses = dict(rollout_common.validate_environment("sandbox", candidate))
                self.assertEqual(statuses[name], "malformed")

    def test_missing_required_configuration_returns_nonzero(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(environment_command.main(["--mode", "sandbox"]), 1)


class SmokeToolingTests(unittest.TestCase):
    def test_url_validation_rejects_unsafe_or_malformed_values(self):
        for value in (
            "",
            "example.com",
            "ftp://example.com",
            "http://example.com",
            "https://user:password@example.com",
            "https://example.com/path",
            "https://example.com?secret=value",
            "https://example.com#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rollout_common.validate_base_url(value)
        self.assertEqual(rollout_common.validate_base_url("https://EXAMPLE.com/"), "https://example.com")
        self.assertEqual(
            rollout_common.validate_base_url("http://127.0.0.1:5000", allow_http_localhost=True),
            "http://127.0.0.1:5000",
        )

    def test_default_smoke_configuration_is_non_destructive(self):
        args = http_smoke_test.build_parser().parse_args(["--base-url", "https://example.com"])
        self.assertIsNone(args.salon_slug)
        self.assertIsNone(args.auth_cookie_env)
        source = Path(http_smoke_test.__file__).read_text(encoding="utf-8")
        self.assertNotIn('method="DELETE"', source)
        self.assertNotIn('method="PUT"', source)

    def test_base_url_is_required(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(http_smoke_test.main([]), 2)


class ShellSafetyTests(unittest.TestCase):
    def test_shell_scripts_use_strict_mode_and_no_database_destructive_commands(self):
        for filename in ("validate_release.sh", "create_postgres_backup.sh"):
            source = (SCRIPTS_DIRECTORY / filename).read_text(encoding="utf-8")
            self.assertIn("set -euo pipefail", source)
            self.assertNotRegex(source, r"(?i)\b(dropdb|truncate|pg_restore\s+--clean)\b")

    def test_backup_requires_configuration_and_production_opt_in(self):
        script = SCRIPTS_DIRECTORY / "create_postgres_backup.sh"
        missing = subprocess.run(
            [str(script), "--output-dir", "/tmp", "--environment", "sandbox"],
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("DATABASE_URL: missing", missing.stderr)
        source = script.read_text(encoding="utf-8")
        self.assertIn('"$production_confirmation" != "BACKUP-PRODUCTION"', source)


if __name__ == "__main__":
    unittest.main()
