"""Regressions for the diff scanner's credential detection and output boundary."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "secret_diff", Path(__file__).resolve().parents[1] / "tools/check_secret_diff.py"
)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class SecretDiffTests(unittest.TestCase):
    def test_credentials_are_found_without_returning_the_value(self):
        for secret in ["gh" + "p_" + "A" * 36, "GOCSP" + "X-" + "x" * 28,
                       "ctb" + "_" + "A" * 52,
                       "SESSION_SECRET=" + "x" * 32,
                       "BACKUP_ENCRYPTION_KEY=" + "x" * 43 + "=",
                       "OFFSITE_B2_KEY_ID=" + "x" * 25,
                       "OFFSITE_B2_APPLICATION_KEY=" + "x" * 31,
                       "https://sm.hetrixtools.net/hb/?s=" + "fictional" * 4,
                       "-----BEGIN " + "PRIVATE KEY-----"]:
            with self.subTest(kind=secret[:4]):
                result = scanner.findings("+++ b/config\n@@ -0,0 +8,1 @@\n+" + secret)
                self.assertEqual(result, [("config", 8)])
                self.assertNotIn(secret, repr(result))

    def test_removed_lines_and_empty_examples_are_not_reported(self):
        self.assertEqual(scanner.findings(
            "+++ b/config\n@@ -3,1 +3,2 @@\n-SESSION_SECRET=" + "x" * 40
            + "\n+SESSION_SECRET=\n+# Set using a generated secret\n"
        ), [])


if __name__ == "__main__":
    unittest.main()
