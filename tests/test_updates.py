import unittest
from unittest.mock import patch

import update_utils


class UpdateTests(unittest.TestCase):
    def test_version_comparison_accepts_v_prefix(self):
        self.assertTrue(update_utils.is_newer_version("V1.1.0", "1.0.0"))
        self.assertTrue(update_utils.is_newer_version("2.0", "1.9.9"))
        self.assertFalse(update_utils.is_newer_version("V1.0.0", "1.0.0"))
        self.assertFalse(update_utils.is_newer_version("0.9.9", "1.0.0"))
        self.assertFalse(update_utils.is_newer_version("latest", "1.0.0"))

    @patch("update_utils.sys.frozen", True, create=True)
    @patch("update_utils._read_json")
    def test_check_for_update_selects_release_exe(self, read_json):
        read_json.return_value = {
            "tag_name": "V1.1.0",
            "name": "Version 1.1.0",
            "html_url": "https://github.com/example/release",
            "published_at": "2026-08-15T10:00:00Z",
            "assets": [{"name": "NetworkManager.exe"}],
        }

        result = update_utils.check_for_update()

        self.assertTrue(result["available"])
        self.assertTrue(result["can_auto_update"])
        self.assertEqual(result["latest_version"], "1.1.0")
        self.assertNotIn("error", result)

    @patch("update_utils._read_json")
    def test_check_rejects_release_without_supported_exe(self, read_json):
        read_json.return_value = {
            "tag_name": "V1.1.0",
            "assets": [{"name": "source.zip"}],
        }

        result = update_utils.check_for_update()

        self.assertTrue(result["available"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
