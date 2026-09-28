import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from http_test_support import authorized_client
import learned_connection_presets as presets


class LearnedConnectionPresetTests(unittest.TestCase):
    def test_learned_setting_overrides_vendor_hint_without_saving_ip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "presets.json"
            with patch.object(presets, "CONNECTION_PRESETS_FILE", path):
                self.assertTrue(presets.learn_preset("Lumens Digital Optics Inc.", "tcp", "5000"))
                self.assertTrue(presets.learn_preset("lumens digital optics inc.", "telnet", "2323"))
                record = presets.get_learned_preset("LUMENS DIGITAL OPTICS INC.")
                self.assertEqual((record["protocol"], record["port"]), ("telnet", 2323))
                self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))), 1)
                self.assertNotIn("192.168", path.read_text(encoding="utf-8"))
                response = authorized_client(app).get(
                    "/connection-test?target=192.168.1.50&manufacturer=Lumens%20Digital%20Optics%20Inc."
                )
                self.assertIn(b'data-learned-protocol="telnet"', response.data)
                self.assertIn(b'data-learned-port="2323"', response.data)

    def test_only_confirmed_scanned_tcp_connection_is_learned(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "presets.json"
            payload = {"protocol": "tcp", "host": "192.168.1.50", "port": "5000"}
            scan = {"results": [{"ip": "192.168.1.50", "manufacturer": "Lumens Digital Optics Inc."}]}
            with patch.object(presets, "CONNECTION_PRESETS_FILE", path), \
                 patch("routes.get_scan_status", return_value=scan), \
                 patch("routes.start_connection", return_value=(True, "Connection opened.")), \
                 patch("routes.get_connection_status", return_value={
                     "connected": True, "running": True, "target": "192.168.1.50:5000"
                 }):
                response = authorized_client(app).post("/connection-test/start", json=payload)
                self.assertTrue(response.get_json()["preset_learned"])
                self.assertEqual(presets.get_learned_preset("Lumens Digital Optics Inc.")["port"], 5000)

                path.unlink()
                udp = authorized_client(app).post("/connection-test/start", json={**payload, "protocol": "udp"})
                self.assertFalse(udp.get_json()["preset_learned"])
                self.assertFalse(path.exists())

                with patch("routes.get_connection_status", return_value={
                    "connected": False, "running": False, "target": "192.168.1.50:5000"
                }):
                    failed = authorized_client(app).post("/connection-test/start", json=payload)
                self.assertFalse(failed.get_json()["preset_learned"])
                self.assertFalse(path.exists())

    def test_unknown_manufacturer_and_unconfirmed_protocol_are_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "presets.json"
            with patch.object(presets, "CONNECTION_PRESETS_FILE", path):
                self.assertFalse(presets.learn_preset("Looking up...", "tcp", 5000))
                self.assertFalse(presets.learn_preset("IEEE Registration Authority", "tcp", 5000))
                self.assertFalse(presets.learn_preset("Lumens", "udp", 5000))
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
