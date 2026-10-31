"""Boundary checks for the source-only reproducibility helper."""
import unittest

from verify_demo import compare_demo


class DemoComparisonTests(unittest.TestCase):
    def test_only_newlines_may_change(self):
        expected = b'{\n  "manifest": {"hash": "original"},\n  "fit": "1/2"\n}\n'
        result = compare_demo(expected.replace(b"\n", b"\r\n"), expected)
        self.assertTrue(result["passed"])
        self.assertFalse(result["raw_bytes_equal"])

    def test_changed_manifest_or_score_fails(self):
        expected = b'{"manifest":{"hash":"original"},"fit":"1/2"}\n'
        for altered in (expected.replace(b"original", b"changed"), expected.replace(b"1/2", b"3/4")):
            with self.subTest(altered=altered):
                self.assertFalse(compare_demo(altered, expected)["passed"])

    def test_missing_manifest_is_refused(self):
        with self.assertRaises(ValueError):
            compare_demo(b'{}', b'{}')


if __name__ == "__main__":
    unittest.main()
