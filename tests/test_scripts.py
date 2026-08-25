import unittest
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import script_history
import script_utils
from app import create_app


class ScriptValidationTests(unittest.TestCase):
    def test_normalizes_target_command_and_delay(self):
        blocks = script_utils._normalize_blocks([
            {
                "type": "target",
                "targets": "192.168.1.10\n192.168.1.10\nprojector.local",
                "protocol": "tcp",
                "port": "23",
                "mode": "sequential",
                "device_delay": "0.5",
            },
            {"type": "command", "value": "PWR ON", "add_cr": True},
            {"type": "delay", "duration": "2"},
        ])

        self.assertEqual(blocks[0]["targets"], ["192.168.1.10", "projector.local"])
        self.assertEqual(blocks[0]["port"], 23)
        self.assertEqual(blocks[0]["device_delay"], 0.5)
        self.assertTrue(blocks[1]["add_cr"])
        self.assertEqual(blocks[2]["duration"], 2.0)

    def test_rejects_command_before_target(self):
        with self.assertRaisesRegex(ValueError, "Target block"):
            script_utils._normalize_blocks([{"type": "command", "value": "PWR ON"}])

    def test_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            script_utils._normalize_blocks([
                {"type": "target", "targets": "192.168.1.10", "protocol": "tcp", "port": 70000}
            ])

    def test_ssh_prompt_detection_does_not_accept_command_echo(self):
        self.assertFalse(script_utils._ends_with_shell_prompt(b"ipt\r\n"))
        self.assertFalse(script_utils._ends_with_shell_prompt(b"IP Table for program 1\r\n"))
        self.assertTrue(script_utils._ends_with_shell_prompt(b"IP Table for program 1\r\nRMC4> "))
        self.assertTrue(script_utils._ends_with_shell_prompt(b"user@host:~$ "))

    @patch("script_utils._TargetConnection")
    def test_runner_connects_sends_and_logs_response(self, connection_type):
        connection = connection_type.return_value
        connection.label = "192.168.1.10:23"
        connection.command_sent.is_set.return_value = False
        blocks = script_utils._normalize_blocks([
            {"type": "target", "targets": "192.168.1.10", "protocol": "tcp", "port": 23},
            {"type": "command", "value": "PWR ON", "add_cr": True},
        ])
        script_utils._stop.clear()
        script_utils._pause.clear()

        script_utils._run_script(blocks)

        connection.connect.assert_called_once()
        connection.send.assert_called_once_with(b"PWR ON\r")
        connection.close.assert_called_once()
        messages = [item["message"] for item in script_utils.get_script_status()["output"]]
        self.assertTrue(any(message.startswith("TX 192.168.1.10:23") for message in messages))
        self.assertEqual(messages[-1], "Script completed.")

    def test_delayed_response_is_received_after_last_command(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def serve_once():
            connection, _address = server.accept()
            try:
                connection.recv(4096)
                time.sleep(0.15)
                connection.sendall(b"STATUS\r\n")
                time.sleep(0.15)
                connection.sendall(b"DELAYED OK\r\n")
            finally:
                connection.close()
                server.close()

        server_thread = threading.Thread(target=serve_once, daemon=True)
        server_thread.start()
        with script_utils._lock:
            script_utils._state["output"].clear()
        script_utils._stop.clear()
        script_utils._pause.clear()
        blocks = script_utils._normalize_blocks([
            {"type": "target", "targets": "127.0.0.1", "protocol": "tcp", "port": port},
            {"type": "command", "value": "STATUS", "add_lf": True},
        ])

        script_utils._run_script(blocks)
        server_thread.join(timeout=1)

        messages = [item["message"] for item in script_utils.get_script_status()["output"]]
        self.assertIn(f"RX 127.0.0.1:{port}: DELAYED OK\r\n", messages)


class ScriptHistoryTests(unittest.TestCase):
    def test_save_load_and_delete_never_persists_password(self):
        original_file = script_history.SCRIPT_HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                script_history.SCRIPT_HISTORY_FILE = Path(directory) / "scripts.json"
                success, _message = script_history.save_script("Room startup", [
                    {
                        "type": "target", "targets": "192.168.1.50", "protocol": "ssh",
                        "port": 22, "username": "admin", "password": "secret",
                    },
                    {"type": "command", "value": "reboot", "add_lf": True},
                ])
                self.assertTrue(success)
                entry = script_history.get_script("Room startup")
                self.assertEqual(entry["blocks"][0]["password"], "")
                self.assertNotIn("secret", script_history.SCRIPT_HISTORY_FILE.read_text(encoding="utf-8"))
                self.assertEqual(script_history.list_scripts()[0]["block_count"], 2)

                success, _message = script_history.delete_script("Room startup")
                self.assertTrue(success)
                self.assertEqual(script_history.list_scripts(), [])
        finally:
            script_history.SCRIPT_HISTORY_FILE = original_file


class ScriptRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

    def test_scripts_page_loads(self):
        response = self.client.get("/scripts")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Script canvas", response.data)

    @patch("routes.start_script", return_value=(False, "Invalid script"))
    @patch("routes.get_script_status", return_value={"running": False, "output": []})
    def test_start_returns_validation_error(self, _status, _start):
        response = self.client.post("/scripts/start", json={"blocks": []})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
