import tempfile
import unittest
from pathlib import Path

from utils import prefixes


class PrefixSettingsTests(unittest.TestCase):
    def test_missing_prefix_uses_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefixes.json"

            self.assertEqual(prefixes.get_prefix(None, path=path), "!")
            self.assertEqual(prefixes.get_prefix(123456789, path=path), "!")

    def test_set_prefix_persists_for_one_guild(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefixes.json"

            saved = prefixes.set_prefix(123456789, "?", path=path)

            self.assertEqual(saved, "?")
            self.assertEqual(prefixes.get_prefix(123456789, path=path), "?")
            self.assertEqual(prefixes.get_prefix(987654321, path=path), "!")

    def test_validate_prefix_rejects_bad_values(self):
        bad_values = ["", "   ", "two words", "\n", "x" * 11]

        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prefixes.normalize_prefix(value)

    def test_validate_prefix_strips_good_values(self):
        self.assertEqual(prefixes.normalize_prefix("  ??  "), "??")


if __name__ == "__main__":
    unittest.main()
