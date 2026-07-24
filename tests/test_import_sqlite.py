import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import import_sqlite


class ImportCredentialTests(unittest.TestCase):
    def test_missing_import_credential_fails_without_a_fallback(self):
        for missing_value in ("", "   "):
            with (
                self.subTest(missing_value=repr(missing_value)),
                patch.object(import_sqlite.os.environ, "get", return_value=missing_value),
                self.assertRaisesRegex(SystemExit, "IMPORT_OWNER_PASSWORD is required"),
            ):
                import_sqlite.import_owner_password()

    def test_explicit_import_credential_is_not_printed(self):
        credential = "unique-test-credential"
        output = io.StringIO()
        with (
            patch.object(import_sqlite.os.environ, "get", return_value=credential),
            redirect_stdout(output),
        ):
            self.assertEqual(import_sqlite.import_owner_password(), credential)
        self.assertNotIn(credential, output.getvalue())

    def test_import_source_contains_no_password_fallback_or_password_output(self):
        source = Path("scripts/import_sqlite.py").read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_OWNER_PASSWORD", source)
        self.assertNotIn("Temporary owner password", source)
        self.assertNotRegex(
            source,
            re.compile(r"print\([^\n]*(password|credential|secret)", re.IGNORECASE),
        )


if __name__ == "__main__":
    unittest.main()
