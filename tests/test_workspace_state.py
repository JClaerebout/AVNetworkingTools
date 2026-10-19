import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
import workspace_state as workspace
from http_test_support import authorized_client


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "workspace.json"
        self.override = patch.object(workspace, "STATE_FILE", self.path)
        self.override.start()
        self.addCleanup(self.override.stop)

    def test_drafts_survive_new_app_and_strip_credentials(self):
        client = authorized_client(app.create_app())
        key = "avNetworkingTools:page-fields:/connection-test"
        response = client.post('/api/workspace/draft', json={"key": key, "value": json.dumps({
            "id:connHost": {"value": "192.0.2.1"}, "id:connPassword": {"value": "secret"}})})
        self.assertEqual(response.status_code, 200)
        html = app.create_app().test_client().get('/connection-test').text
        self.assertIn("192.0.2.1", html)
        self.assertNotIn('connPassword\\\"', self.path.read_text())
        self.assertNotIn('secret', self.path.read_text())

    def test_script_credentials_and_unknown_keys(self):
        workspace.save_draft("avNetworkingTools:scriptDraft:v1", '[{"type":"target","password":"private"}]')
        self.assertNotIn("private", self.path.read_text())
        with self.assertRaises(ValueError):
            workspace.save_draft("unexpected", "value")

    def test_write_is_authenticated(self):
        self.assertEqual(app.create_app().test_client().post('/api/workspace/draft', json={}).status_code, 403)

    def test_corrupt_primary_recovers_backup(self):
        workspace.save_draft("avNetworkingTools:theme", "dark")
        self.path.write_text("invalid")
        with self.assertWarns(RuntimeWarning):
            self.assertEqual(workspace.read_section("drafts")["avNetworkingTools:theme"], "dark")

    def test_scan_and_logs_restore_without_starting_tasks(self):
        import scan_utils as scan
        import connection_utils as connection
        import script_utils as script
        with patch.object(scan, '_scan_results', [{"ip": "192.0.2.2", "mac": "AA"}]), \
             patch.object(connection, '_conn_output', connection.deque(["previous reply"], maxlen=1000)), \
             patch.dict(script._state, output=script.deque([{"message": "previous run"}], maxlen=1500)):
            workspace.snapshot_runtime()
            scan._scan_results = []
            connection._conn_output.clear()
            script._state['output'].clear()
            with patch.object(scan, 'start_scan') as start_scan, patch.object(connection, 'start_connection') as connect, patch.object(script, 'start_script') as run:
                workspace.restore_runtime()
            self.assertEqual(scan.get_scan_status()['results'][0]['ip'], '192.0.2.2')
            self.assertEqual(connection.get_connection_status()['output'], ['previous reply'])
            self.assertEqual(script.get_script_status()['output'][0]['message'], 'previous run')
            for action in (start_scan, connect, run):
                action.assert_not_called()
            self.assertFalse(connection.get_connection_status()['connected'])
            self.assertFalse(script.get_script_status()['running'])


if __name__ == '__main__':
    unittest.main()
