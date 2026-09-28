from http_test_support import authorized_client
import unittest
from unittest.mock import patch

from app import app
from nic_utils import get_interface_metrics, set_interface_metric


class NicPriorityTests(unittest.TestCase):
    @patch('nic_utils.run_powershell')
    def test_invalid_metric_never_runs_command(self, run):
        for index, metric in [('1; Stop-Process', '2'), ('0', '2'), ('1', '0'), ('1', '10000'), ('1', '1;whoami')]:
            self.assertFalse(set_interface_metric(index, metric)[0])
        run.assert_not_called()

    @patch('nic_utils.run_powershell', return_value=(0, '', ''))
    def test_manual_and_automatic_metric(self, run):
        self.assertTrue(set_interface_metric('12', '25')[0])
        self.assertIn('-InterfaceIndex 12 -AddressFamily IPv4 -AutomaticMetric Disabled -InterfaceMetric 25', run.call_args.args[0])
        self.assertTrue(set_interface_metric('12', 'auto')[0])
        self.assertIn('-AutomaticMetric Enabled', run.call_args.args[0])
        self.assertNotIn('-InterfaceMetric', run.call_args.args[0])

    @patch('nic_utils.run_powershell', return_value=(1, '', 'Access denied'))
    def test_command_failure_is_reported(self, run):
        self.assertEqual(set_interface_metric('12', '25'), (False, 'Access denied'))
        self.assertEqual(get_interface_metrics(), {})

    @patch('nic_utils.run_powershell', return_value=(0, '{"InterfaceIndex":12,"InterfaceMetric":25,"AutomaticMetric":1}', ''))
    def test_single_interface_json(self, run):
        self.assertEqual(get_interface_metrics(), {12: {'metric': 25, 'automatic_metric': True}})

    @patch('routes.get_nics', return_value=[{'if_index': 12, 'ip': '192.168.1.2'}])
    def test_status_route(self, get_nics):
        response = authorized_client(app).get('/nics/status')
        self.assertEqual(response.get_json()['nics'][0]['ip'], '192.168.1.2')

    def test_converter_page(self):
        response = authorized_client(app).get('/converter')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'converterAscii', response.data)
