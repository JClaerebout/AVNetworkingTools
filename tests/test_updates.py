import tempfile
import unittest
from pathlib import Path
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
            "tag_name": "V1.3.0",
            "name": "Version 1.3.0",
            "html_url": "https://github.com/example/release",
            "published_at": "2026-08-15T10:00:00Z",
            "assets": [{"name": "AVNetKit.exe"}],
        }

        result = update_utils.check_for_update()

        self.assertTrue(result["available"])
        self.assertTrue(result["can_auto_update"])
        self.assertEqual(result["latest_version"], "1.3.0")
        self.assertNotIn("error", result)

    @patch("update_utils._read_json")
    def test_check_rejects_release_without_supported_exe(self, read_json):
        read_json.return_value = {
            "tag_name": "V1.3.0",
            "assets": [{"name": "source.zip"}],
        }

        result = update_utils.check_for_update()

        self.assertTrue(result["available"])
        self.assertIn("error", result)

    def test_install_helper_waits_for_launcher_and_records_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "download" / "AVNetKit.exe"
            target = temp_path / "installed" / "AVNetKit.exe"
            source.parent.mkdir()
            target.parent.mkdir()
            source.write_bytes(b"new executable")
            target.write_bytes(b"old executable")

            with (
                patch("update_utils.sys.frozen", True, create=True),
                patch("update_utils.sys.executable", str(target)),
                patch("update_utils.BASE_DIR", temp_path),
                patch("update_utils.get_update_state", return_value={"status": "ready", "downloaded_path": str(source)}),
                patch("update_utils.os.getpid", return_value=1234),
                patch("update_utils.os.getppid", return_value=1233),
                patch("update_utils.subprocess.Popen") as popen,
                patch("update_utils.threading.Timer") as timer,
            ):
                success, _message = update_utils.install_downloaded_update()

            self.assertTrue(success)
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("-AppProcessId") + 1], "1234")
            self.assertEqual(command[command.index("-LauncherProcessId") + 1], "1233")
            self.assertEqual(command[command.index("-Target") + 1], str(target.resolve()))
            helper_script = (source.parent / "install-update.ps1").read_text(encoding="utf-8")
            self.assertIn("for ($attempt = 1; $attempt -le 120; $attempt++)", helper_script)
            self.assertIn('Where-Object { $_.Name -like "_PYI_*" }', helper_script)
            self.assertIn('$env:PYINSTALLER_RESET_ENVIRONMENT = "1"', helper_script)
            self.assertIn("-WorkingDirectory $workingDirectory", helper_script)
            self.assertIn(str(target.resolve()), (temp_path / "update.log").read_text(encoding="utf-8"))
            timer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
