import unittest
from unittest.mock import patch

import activity_utils
from app import create_app


class ActivityTests(unittest.TestCase):
    def test_active_and_paused_tasks_have_tool_links(self):
        with patch.object(activity_utils, 'get_scan_activity', return_value={
            'scan': True, 'lookup': False, 'monitor': True, 'monitor_paused': True,
        }), patch.object(activity_utils, 'get_ping_activity', return_value=True), \
             patch.object(activity_utils, 'get_connection_activity', return_value='Connected'), \
             patch.object(activity_utils, 'get_script_activity', return_value=(True, False)), \
             patch.object(activity_utils, 'get_wifi_activity', return_value=True), \
             patch.object(activity_utils, 'get_multicast_activity', return_value=True):
            response = create_app().test_client().get('/api/activity')
        self.assertEqual(response.status_code, 200)
        tasks = {task['key']: task for task in response.json['tasks']}
        self.assertEqual(set(tasks), {'scan', 'ping', 'connection', 'script', 'wifi', 'health'})
        self.assertEqual(tasks['scan']['label'], 'IP Scan · Scanning')
        self.assertEqual(tasks['connection']['url'], '/connection-test')
        self.assertEqual(tasks['health']['url'], '/multicast')

    def test_lookup_or_monitor_keeps_ip_scan_visible(self):
        for flags, expected in (
            ({'scan': False, 'lookup': True, 'monitor': False, 'monitor_paused': False}, 'IP Scan · Looking up details'),
            ({'scan': False, 'lookup': False, 'monitor': True, 'monitor_paused': True}, 'IP Scan · Monitor paused'),
        ):
            with self.subTest(flags=flags), patch.object(activity_utils, 'get_scan_activity', return_value=flags), \
                 patch.object(activity_utils, 'get_ping_activity', return_value=False), \
                 patch.object(activity_utils, 'get_connection_activity', return_value='Disconnected'), \
                 patch.object(activity_utils, 'get_script_activity', return_value=(False, False)), \
                 patch.object(activity_utils, 'get_wifi_activity', return_value=False), \
                 patch.object(activity_utils, 'get_multicast_activity', return_value=True):
                tasks = create_app().test_client().get('/api/activity').json['tasks']
            self.assertEqual({task['key'] for task in tasks}, {'scan', 'health'})
            self.assertEqual(tasks[0]['label'], expected)

    def test_inactive_tasks_are_not_reported(self):
        with patch.object(activity_utils, 'get_scan_activity', return_value={
            'scan': False, 'lookup': False, 'monitor': False, 'monitor_paused': False,
        }), patch.object(activity_utils, 'get_ping_activity', return_value=False), \
             patch.object(activity_utils, 'get_connection_activity', return_value='Disconnected'), \
             patch.object(activity_utils, 'get_script_activity', return_value=(False, False)), \
             patch.object(activity_utils, 'get_wifi_activity', return_value=False), \
             patch.object(activity_utils, 'get_multicast_activity', return_value=False):
            response = create_app().test_client().get('/api/activity')
        self.assertEqual(response.json, {'tasks': []})

    def test_header_contains_activity_container(self):
        response = create_app().test_client().get('/converter')
        self.assertIn(b'id="activeTasks"', response.data)
        self.assertIn(b'activity.js', response.data)


if __name__ == '__main__':
    unittest.main()
