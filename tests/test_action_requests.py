import unittest
from unittest.mock import patch

from app import create_app


class ActionRequestTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        self.client = app.test_client()
        self.headers = {"X-AV-Token": app.config["ACTION_TOKEN"]}

    def test_non_object_json_is_rejected_before_actions(self):
        with patch("routes.start_ping") as ping, patch("routes.start_connection") as connection, patch("routes.start_script") as script:
            for path in ("/ping/start", "/connection-test/start", "/connection-test/send", "/scripts/start", "/scripts/pause"):
                response = self.client.post(path, json=["wrong"], headers=self.headers)
                self.assertEqual(response.status_code, 400, path)
                self.assertIn("JSON object", response.json["message"])
            ping.assert_not_called()
            connection.assert_not_called()
            script.assert_not_called()

    def test_invalid_field_types_are_clear_errors(self):
        cases = (("/ping/start", {"ip": 4}),
                 ("/connection-test/start", {"host": ["wrong"]}),
                 ("/connection-test/send", {"data": 4}),
                 ("/scripts/pause", {"paused": "yes"}))
        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, json=data, headers=self.headers)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["success"])


if __name__ == "__main__":
    unittest.main()
