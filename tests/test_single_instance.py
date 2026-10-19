import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from single_instance import AlreadyRunningError, desktop_instance


class SingleInstanceTests(unittest.TestCase):
    def test_duplicate_rejected_and_normal_exit_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop.lock"
            with desktop_instance(path):
                with self.assertRaises(AlreadyRunningError):
                    with desktop_instance(path):
                        self.fail("Second instance acquired lock")
            with desktop_instance(path):
                pass

    def test_exception_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop.lock"
            with self.assertRaises(ValueError):
                with desktop_instance(path):
                    raise ValueError("Startup failed")
            with desktop_instance(path):
                pass

    def test_process_exit_releases_lock_without_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop.lock"
            code = (
                "import sys; from single_instance import desktop_instance; "
                "lock = desktop_instance(sys.argv[1]); lock.__enter__(); "
                "print('ready', flush=True); sys.stdin.read()"
            )
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", code, str(path)],
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                with self.assertRaises(AlreadyRunningError):
                    with desktop_instance(path):
                        self.fail("Second process acquired lock")
                process.kill()
                process.wait(timeout=5)
                with desktop_instance(path):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
