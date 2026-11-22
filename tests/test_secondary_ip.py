import copy
import ctypes
import unittest
from unittest.mock import patch, Mock
import secondary_ip as secondary


def adapter(dhcp=True, entries=None):
    return dict(name='Ethernet', if_index=12, device='en4', ip='10.0.0.2',
                subnet='255.255.255.0', gateway='10.0.0.1', dns=['10.0.0.1'],
                dhcp_raw='Enabled' if dhcp else 'Disabled', secondary_safe=True,
                secondary_ips=entries or [])


def alias(ip='192.168.0.100'):
    return dict(ip=ip, prefix=24, active=True, removable=True)


class SecondaryTests(unittest.TestCase):
    def setUp(self):
        local = patch.object(secondary, '_all_local_ipv4', return_value=set())
        arp = patch('scan_utils._arp_probe', return_value='')
        local.start(); arp.start()
        self.addCleanup(local.stop); self.addCleanup(arp.stop)

    def test_validation(self):
        for ip, mask in [('127.0.0.1', '255.255.255.0'), ('224.1.1.1', '255.255.255.0'),
                         ('0.0.0.0', '255.255.255.0'), ('192.168.0.0', '255.255.255.0'),
                         ('192.168.0.255', '255.255.255.0'), ('999.0.0.1', '255.255.255.0'),
                         ('192.168.0.1', '255.0.255.0'), ('192.168.0.1', '0.0.0.255')]:
            with self.subTest(ip=ip, mask=mask), self.assertRaises(ValueError):
                secondary.validate_address(ip, mask)
        self.assertEqual(secondary.validate_address('192.168.0.100', '255.255.255.0').prefixlen, 24)

    def test_dhcp_and_static_add(self):
        for dhcp in (True, False):
            before = adapter(dhcp, [alias('172.16.1.2')])
            after = copy.deepcopy(before)
            after['secondary_ips'].append(alias())
            with patch('nic_utils.get_nics', side_effect=[[before], [after]]), patch.object(secondary, '_change') as change:
                self.assertTrue(secondary.change_secondary(12, '192.168.0.100', '255.255.255.0')[0])
                change.assert_called_once_with(before, '192.168.0.100', 24, False)

    def test_duplicate_and_primary_protection(self):
        before = adapter(entries=[alias()])
        with patch('nic_utils.get_nics', return_value=[before]), patch.object(secondary, '_change') as change:
            self.assertFalse(secondary.change_secondary(12, '192.168.0.100', '255.255.255.0')[0])
            self.assertFalse(secondary.change_secondary(12, before['ip'], remove=True)[0])
            self.assertFalse(secondary.change_secondary(12, '192.168.9.2', remove=True)[0])
            change.assert_not_called()

    def test_remove(self):
        before = adapter(entries=[alias()])
        with patch('nic_utils.get_nics', side_effect=[[before], [adapter()]]), patch.object(secondary, '_change') as change:
            self.assertTrue(secondary.change_secondary(12, '192.168.0.100', remove=True)[0])
            self.assertTrue(change.call_args.args[-1])

    def test_failed_native_operation(self):
        with patch('nic_utils.get_nics', return_value=[adapter()]), patch.object(secondary, '_change', side_effect=RuntimeError('Access denied')):
            self.assertIn('Access denied', secondary.change_secondary(12, '192.168.0.100', '255.255.255.0')[1])

    def test_primary_change_rolls_back_alias(self):
        after = adapter(entries=[alias()]); after['gateway'] = '10.0.0.3'
        with patch('nic_utils.get_nics', side_effect=[[adapter()], [after]]), patch.object(secondary, '_change') as change:
            self.assertFalse(secondary.change_secondary(12, '192.168.0.100', '255.255.255.0')[0])
            self.assertEqual([c.args[-1] for c in change.call_args_list], [False, True])

    def test_windows_primary_uses_origin_not_address_order(self):
        rows = [dict(InterfaceIndex=12, IPAddress='192.168.0.100', PrefixLength=24, PrefixOrigin='Manual', SkipAsSource=True, AddressState='Preferred'),
                dict(InterfaceIndex=12, IPAddress='10.0.0.2', PrefixLength=24, PrefixOrigin='Dhcp', SkipAsSource=False, AddressState='Preferred')]
        nic = adapter(); nic['ip'] = '192.168.0.100'
        with patch.object(secondary, 'windows_addresses', return_value=rows):
            result = secondary.enrich_windows([nic])[0]
        self.assertEqual(result['ip'], '10.0.0.2')
        self.assertEqual(result['secondary_ips'], [alias()])

    def test_macos_reads_actual_aliases(self):
        output = 'inet 10.0.0.2 netmask 0xffffff00\n inet 192.168.0.100 netmask 0xffffff00'
        with patch('platform_backend.macos.nic._run', return_value=(0, output, '')):
            self.assertEqual(secondary.mac_addresses('en4', '10.0.0.2'), [alias()])
        with patch('platform_backend.macos.nic._run', return_value=(0, 'inet 10.0.0.2 netmask 0xffffff00', '')):
            self.assertEqual(secondary.mac_addresses('en4', '10.0.0.2'), [])

    def test_macos_authorization_and_exact_command(self):
        with patch.object(secondary.sys, 'platform', 'darwin'), patch('platform_backend.macos.nic._run', side_effect=[(1, '', 'permission denied'), (0, '', '')]) as run:
            secondary._change(adapter(), '192.168.0.100', 24, False)
            self.assertEqual(run.call_args.args[0], ['/sbin/ifconfig', 'en4', 'inet', '192.168.0.100', 'netmask', '255.255.255.0', 'alias'])
            self.assertTrue(run.call_args.kwargs['privileged'])

    def test_windows_abi_and_native_call(self):
        self.assertEqual(ctypes.sizeof(secondary._Row), 80)
        self.assertEqual(secondary._Row.index.offset, 40)
        self.assertEqual(secondary._Row.skip.offset, 61)
        api = Mock(); api.CreateUnicastIpAddressEntry.return_value = 0
        with patch.object(ctypes, 'WinDLL', return_value=api, create=True):
            secondary._windows_change(adapter(), '192.168.0.100', 24, False)
        row = api.CreateUnicastIpAddressEntry.call_args.args[0]._obj
        self.assertEqual(row.index, 12)
        self.assertEqual(row.skip, 1)
        self.assertEqual(list(row.address.raw)[4:8], [192, 168, 0, 100])

    def test_restore_reconciles_live_aliases(self):
        old = adapter(entries=[alias()])
        with patch('nic_utils.get_nics', return_value=[adapter()]), patch.object(secondary, 'change_secondary', return_value=(True, 'ok')) as change:
            self.assertTrue(secondary.restore_secondary('Ethernet', old)[0])
            change.assert_called_once_with(12, '192.168.0.100', '255.255.255.0')

    def test_preview_warns_before_overlap_without_mutation(self):
        with patch('nic_utils.get_nics', return_value=[adapter()]), patch.object(secondary, '_change') as change:
            ok, message = secondary.change_secondary(12, '10.0.0.3', '255.255.255.0', preview=True)
            self.assertTrue(ok)
            self.assertIn('Overlapping', message)
            change.assert_not_called()

    def test_duplicate_on_hidden_interface(self):
        with patch('nic_utils.get_nics', return_value=[adapter()]), patch.object(secondary, '_all_local_ipv4', return_value={'192.168.0.100'}), patch.object(secondary, '_change') as change:
            self.assertFalse(secondary.change_secondary(12, '192.168.0.100', '255.255.255.0')[0])
            change.assert_not_called()

    def test_secondary_scan_source_and_ping_selection(self):
        import scan_utils
        before = adapter(entries=[alias()]); before['status'] = 'dhcp'
        with patch('scan_utils.get_nics', return_value=[before]):
            sources = scan_utils.get_scannable_nics()
        self.assertEqual([n['ip'] for n in sources], ['10.0.0.2', '192.168.0.100'])
        with patch('nic_utils.get_nics', return_value=[before]):
            self.assertEqual(secondary.source_for_target('192.168.0.60'), '192.168.0.100')
            self.assertEqual(secondary.source_for_target('10.0.0.60'), '')
            self.assertEqual(secondary.source_for_target('switch.local'), '')

    def test_route_requires_existing_request_protection(self):
        from app import app
        from http_test_support import authorized_client
        with patch.object(secondary, 'change_secondary', return_value=(True, 'Added')) as change:
            self.assertEqual(app.test_client().post('/nics/secondary', data={}).status_code, 403)
            change.assert_not_called()
            response = authorized_client(app).post('/nics/secondary', data={'interface_index': '12', 'ip': '192.168.0.100', 'subnet': '255.255.255.0', 'operation': 'preview'})
            self.assertEqual(response.status_code, 200)
            change.assert_called_once_with('12', '192.168.0.100', '255.255.255.0', False, preview=True, expected_name=None)


    def test_changed_adapter_identity_is_rejected(self):
        with patch('nic_utils.get_nics', return_value=[adapter()]), patch.object(secondary, '_change') as change:
            self.assertFalse(secondary.change_secondary(12, '192.168.0.100', '255.255.255.0', expected_name='Wi-Fi')[0])
            change.assert_not_called()

    def test_windows_read_failure_preserves_primary_and_disables_changes(self):
        with patch.object(secondary, 'windows_addresses', side_effect=RuntimeError('Read failed')):
            nic = secondary.enrich_windows([adapter()])[0]
        self.assertEqual(nic['ip'], '10.0.0.2')
        self.assertFalse(nic['secondary_safe'])
        self.assertEqual(nic['secondary_error'], 'Read failed')

    def test_remove_cannot_succeed_when_readback_failed(self):
        before = adapter(entries=[alias()]); after = adapter()
        after.update(secondary_error='Read failed', secondary_safe=False)
        with patch('nic_utils.get_nics', side_effect=[[before], [after]]), patch.object(secondary, '_change') as change:
            ok, message = secondary.change_secondary(12, '192.168.0.100', remove=True)
            self.assertFalse(ok)
            self.assertIn('Cannot verify', message)
            self.assertEqual([c.args[-1] for c in change.call_args_list], [True, False])
