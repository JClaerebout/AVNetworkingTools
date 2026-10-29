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
        directory_override = patch.object(workspace, "BASE_DIR", Path(self.directory.name))
        directory_override.start()
        self.addCleanup(directory_override.stop)

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

    def test_save_new_reload_keeps_explicit_snapshot(self):
        client = authorized_client(app.create_app())
        key = "avNetworkingTools:page-fields:/ping"
        workspace.save_draft(key, '{"target":{"value":"192.0.2.8"}}')
        workspace.save_draft("avNetworkingTools:theme", "dark")
        with patch('activity_utils.get_active_tasks', return_value=[]), \
             patch.object(workspace, 'snapshot_runtime'), \
             patch.object(workspace, 'restore_runtime') as restore:
            workspace.write_section("runtime", {"ping": {"output": ["saved reply"]}})
            self.assertEqual(client.post('/api/workspace/save', json={}).status_code, 200)
            self.assertEqual(client.post('/api/workspace/new', json={}).status_code, 200)
            self.assertNotIn(key, workspace.read_section("drafts"))
            self.assertEqual(workspace.read_section("drafts")["avNetworkingTools:theme"], "dark")
            self.assertEqual(client.post('/api/workspace/reload', json={}).status_code, 200)
            self.assertIn("192.0.2.8", workspace.read_section("drafts")[key])
            restore.assert_called_with({"ping": {"output": ["saved reply"]}}, replace=True)

    def test_workspace_changes_require_auth_saved_state_and_idle_tasks(self):
        app_instance = app.create_app()
        for action in ('save', 'reload', 'new'):
            self.assertEqual(app_instance.test_client().post('/api/workspace/' + action).status_code, 403)
        client = authorized_client(app_instance)
        with patch('activity_utils.get_active_tasks', return_value=[]):
            self.assertEqual(client.post('/api/workspace/reload', json={}).status_code, 409)
        workspace.save_draft("avNetworkingTools:theme", "dark")
        workspace.saved_workspaces()
        before = self.path.read_text()
        with patch('activity_utils.get_active_tasks', return_value=[{"key": "ping"}]):
            for action in ('reload', 'new'):
                self.assertEqual(client.post('/api/workspace/' + action, json={}).status_code, 409)
        self.assertEqual(self.path.read_text(), before)

    def test_named_workspaces_are_independent_and_delete_does_not_change_current(self):
        workspace.workspace_libraries()  # A fresh install has completed migration before use.
        client = authorized_client(app.create_app())
        key = "avNetworkingTools:page-fields:/ping"
        with patch('activity_utils.get_active_tasks', return_value=[]), \
             patch.object(workspace, 'snapshot_runtime'), \
             patch.object(workspace, 'restore_runtime'):
            for name, target in (("Hall A", "192.0.2.1"), ("Hall B", "192.0.2.2")):
                workspace.save_draft(key, json.dumps({"target": target}))
                self.assertEqual(client.post('/api/workspace/save', json={"name": name}).status_code, 200)
            self.assertEqual(client.get('/api/workspaces').json['workspaces'], ['Hall A', 'Hall B'])
            self.assertEqual(client.post('/api/workspace/save', json={"name": "Hall A"}).status_code, 409)
            self.assertEqual(client.post('/api/workspace/reload', json={"name": "Hall A"}).status_code, 200)
            self.assertIn('192.0.2.1', workspace.read_section('drafts')[key])
            current = workspace.read_section('drafts')
            self.assertEqual(client.post('/api/workspace/delete', json={"name": "Hall A"}).status_code, 200)
            self.assertEqual(workspace.read_section('drafts'), current)
            self.assertEqual(client.get('/api/workspaces').json['workspaces'], ['Hall B'])
            self.assertEqual(client.post('/api/workspace/reload', json={"name": "Hall A"}).status_code, 409)
            self.assertEqual(client.post('/api/workspace/delete', json={"name": "Hall B"}).status_code, 200)
            self.assertEqual(workspace.saved_workspaces(), {})

    def test_libraries_follow_save_load_new_and_survive_restart(self):
        with patch('activity_utils.get_active_tasks', return_value=[]), \
             patch.object(workspace, 'snapshot_runtime'), \
             patch.object(workspace, 'restore_runtime'):
            for filename in workspace.LIBRARY_FILES:
                path = workspace.BASE_DIR / filename
                workspace.update_workspace_list(path, lambda item: True,
                                                lambda items: ([{"name": "Hall A"}], None))
            workspace.workspace_action('save', 'Hall A')
            workspace.workspace_action('new')
            for filename in workspace.LIBRARY_FILES:
                path = workspace.BASE_DIR / filename
                self.assertEqual(workspace.load_workspace_list(path, lambda item: True), [])
                workspace.update_workspace_list(path, lambda item: True,
                                                lambda items: ([{"name": "Hall B"}], None))
            workspace.workspace_action('save', 'Hall B')
            workspace.workspace_action('reload', 'Hall A')
            for filename in workspace.LIBRARY_FILES:
                self.assertEqual(workspace.load_workspace_list(workspace.BASE_DIR / filename, lambda item: True),
                                 [{"name": "Hall A"}])
            workspace.workspace_action('rename', 'Hall A', new_name='Renamed')
            workspace.workspace_action('delete', 'Renamed')
            self.assertEqual(workspace.workspace_libraries()['script_history.json'], [{"name": "Hall A"}])
            workspace.workspace_action('reload', 'Hall B')
            client = app.create_app().test_client()
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'Hall B')
            self.assertEqual(workspace.workspace_libraries()['script_history.json'], [{"name": "Hall B"}])

    def test_shared_libraries_migrate_once_into_existing_saves(self):
        legacy_path = workspace.BASE_DIR / 'ping_history.json'
        legacy_path.write_text('["192.0.2.10"]')
        workspace.write_section('workspaces', {'Original': {'drafts': {}, 'runtime': {}}})
        self.assertEqual(workspace.saved_workspaces()['Original']['libraries']['ping_history.json'], ['192.0.2.10'])
        with patch('activity_utils.get_active_tasks', return_value=[]), \
             patch.object(workspace, 'restore_runtime'):
            workspace.workspace_action('new')
            self.assertEqual(workspace.load_workspace_list(legacy_path, lambda item: True), [])
            workspace.workspace_action('reload', 'Original')
            self.assertEqual(workspace.load_workspace_list(legacy_path, lambda item: True), ['192.0.2.10'])
        self.assertEqual(legacy_path.read_text(), '["192.0.2.10"]')

    def test_upgrade_creates_default_with_all_existing_data_once(self):
        for filename in workspace.LIBRARY_FILES:
            (workspace.BASE_DIR / filename).write_text('[{"name":"existing"}]')
        workspace.write_section('drafts', {'avNetworkingTools:theme': 'dark'})
        workspace.write_section('runtime', {'ping': {'output': ['previous reply']}})
        saves = workspace.saved_workspaces()
        default = saves['Default workspace']
        self.assertEqual(workspace.read_section('current_name'), 'Default workspace')
        self.assertEqual(default['drafts'], workspace.read_section('drafts'))
        self.assertEqual(default['runtime'], workspace.read_section('runtime'))
        for filename in workspace.LIBRARY_FILES:
            self.assertEqual(default['libraries'][filename], [{'name': 'existing'}])
        with patch('activity_utils.get_active_tasks', return_value=[]), \
             patch.object(workspace, 'restore_runtime'):
            workspace.workspace_action('new')
            workspace.workspace_action('reload', 'Default workspace')
            self.assertEqual(workspace.workspace_libraries(), default['libraries'])
            workspace.workspace_action('delete', 'Default workspace')
        self.assertNotIn('Default workspace', workspace.saved_workspaces())
        self.assertTrue(workspace.read_section('default_workspace_migrated'))

    def test_upgrade_preserves_existing_named_workspace(self):
        workspace.write_section('libraries', {'ping_history.json': ['192.0.2.1']})
        workspace.write_section('workspaces', {'Default workspace': {'drafts': {'keep': 'yes'}}})
        workspace.write_section('current_name', 'My workspace')
        self.assertEqual(workspace.saved_workspaces()['Default workspace']['drafts'], {'keep': 'yes'})
        self.assertEqual(workspace.read_section('current_name'), 'My workspace')

    def test_current_name_and_rename_preserve_saved_content(self):
        client = authorized_client(app.create_app())
        with patch('activity_utils.get_active_tasks', return_value=[]), \
             patch.object(workspace, 'snapshot_runtime'), \
             patch.object(workspace, 'restore_runtime'):
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'new workspace')
            workspace.workspace_action('save', 'Hall A')
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'Hall A')
            saved = workspace.saved_workspaces()['Hall A']
            response = client.post('/api/workspace/rename', json={'name': 'Hall A', 'new_name': 'Main hall'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(workspace.saved_workspaces(), {'Main hall': saved})
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'Main hall')
            workspace.workspace_action('save', 'Hall B')
            with self.assertRaises(ValueError):
                workspace.workspace_action('rename', 'Main hall', new_name='Hall B')
            self.assertEqual(len(workspace.saved_workspaces()), 2)
            workspace.workspace_action('new')
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'new workspace')
            workspace.workspace_action('reload', 'Main hall')
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'Main hall')
            workspace.workspace_action('delete', 'Main hall')
            self.assertEqual(client.get('/api/workspaces').json['current_name'], 'Main hall')

    def test_legacy_save_migrates_once_and_names_are_validated(self):
        legacy = {"drafts": {"avNetworkingTools:theme": "dark"}, "runtime": {}}
        workspace.write_section('saved', legacy)
        self.assertEqual(workspace.saved_workspaces()["Saved workspace"]["drafts"], legacy["drafts"])
        self.assertIn("libraries", workspace.saved_workspaces()["Saved workspace"])
        workspace.workspace_action('delete', 'Saved workspace')
        self.assertEqual(workspace.saved_workspaces(), {})
        for name in ('', '   ', 'a' * 101, 42, []):
            with self.assertRaises(ValueError):
                workspace.workspace_action('save', name)
        with patch.object(workspace, 'snapshot_runtime'):
            workspace.workspace_action('save', '  Hall  ')
            with self.assertRaises(ValueError):
                workspace.workspace_action('save', 'Hall')
            workspace.workspace_action('save', 'Hall', overwrite=True)
        self.assertEqual(list(workspace.saved_workspaces()), ['Hall'])

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
                workspace.restore_runtime(replace=True)
                workspace.restore_runtime(replace=True)
            self.assertEqual(scan.get_scan_status()['results'][0]['ip'], '192.0.2.2')
            self.assertEqual(connection.get_connection_status()['output'], ['previous reply'])
            self.assertEqual(script.get_script_status()['output'][0]['message'], 'previous run')
            for action in (start_scan, connect, run):
                action.assert_not_called()
            self.assertFalse(connection.get_connection_status()['connected'])
            self.assertFalse(script.get_script_status()['running'])


if __name__ == '__main__':
    unittest.main()
