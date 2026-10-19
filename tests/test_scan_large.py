import ipaddress
import struct
import threading
import unittest
from unittest.mock import patch

import arp_scan
import scan_utils as scan


class LargeScanTests(unittest.TestCase):
    def test_range_policy(self):
        self.assertEqual(scan._validated_network('169.254.0.0/16', True).num_addresses, 65536)
        for subnet, quick in [('169.254.0.0/16', False), ('10.0.0.0/15', True), ('::/64', True)]:
            with self.assertRaises(ValueError):
                scan._validated_network(subnet, quick)

    def test_arp_reply_from_outside_static_subnet(self):
        source = '192.168.1.10'
        mac = '00:11:22:33:44:55'
        remote = bytes.fromhex('aabbccddeeff')
        frame = (bytes.fromhex('001122334455') + remote + b'\x08\x06' +
                 struct.pack('!HHBBH', 1, 0x0800, 6, 4, 2) + remote +
                 bytes([169, 254, 20, 30]) + bytes.fromhex('001122334455') + bytes([192, 168, 1, 10]))
        network = ipaddress.ip_network('169.254.0.0/16')
        self.assertEqual(arp_scan.reply_address(frame, source, mac, network), ('169.254.20.30', 'AA:BB:CC:DD:EE:FF'))
        self.assertIsNone(arp_scan.reply_address(frame, '192.168.1.11', mac, network))
        request = arp_scan.request_frame(mac, source, '169.254.20.30')
        self.assertEqual(request[:6], b'\xff' * 6)
        self.assertEqual(request[38:42], bytes([169, 254, 20, 30]))

    def test_sweep_cancellation_closes_capture_without_enumerating_range(self):
        stop = threading.Event()
        sent = []
        class Capture:
            closed = False
            def __init__(self, nic): pass
            def send(self, frame):
                sent.append(frame)
                stop.set()
            def receive(self): return None
            def close(self): Capture.closed = True
        progress = []
        arp_scan.sweep({'ip': '192.168.1.10', 'mac': '00:11:22:33:44:55'},
                       ipaddress.ip_network('169.254.0.0/16'), stop,
                       lambda: progress.append(1), lambda *args: None, Capture)
        self.assertEqual(len(sent), 1)
        self.assertTrue(Capture.closed)

    def test_large_scan_disables_lookup(self):
        nic = {'ip': '192.168.1.10', 'mac': '00:11:22:33:44:55', 'network': '192.168.1.0/24', 'device': 'en0'}
        saved = {name: getattr(scan, name) for name in ('_scan_results', '_large_scan_quick_only', '_scan_total', '_scan_done', '_scan_message', '_lookup_running')}
        context = dict(scan._last_scan_context)
        scan._scan_results = []
        scan._scan_stop.clear()
        def sweep(nic, network, stop, progress, found):
            progress()
            found('169.254.20.30', 'AA:BB:CC:DD:EE:FF')
        try:
            with patch.object(scan, '_find_nic', return_value=(True, nic)), patch('arp_scan.sweep', side_effect=sweep):
                scan._scan_worker_impl('Ethernet', '169.254.0.0/16', True)
            self.assertTrue(scan._large_scan_quick_only)
            self.assertEqual(scan._scan_total, 131068)
            self.assertEqual(scan._scan_done, 1)
            self.assertFalse(scan.start_lookup()[0])
            self.assertEqual(scan._scan_results[0]['ip'], '169.254.20.30')
        finally:
            for name, value in saved.items(): setattr(scan, name, value)
            scan._last_scan_context.update(context)

    def test_scheduler_bounds_pending_work_and_stops_submitting(self):
        from concurrent.futures import Future
        nic = {'ip': '10.0.0.1', 'mac': '', 'network': '10.0.0.0/24'}
        saved = {name: getattr(scan, name) for name in ('_scan_results', '_large_scan_quick_only', '_scan_total', '_scan_done', '_scan_message', '_lookup_running')}
        context = dict(scan._last_scan_context)
        submitted = []
        class Executor:
            def __init__(self, max_workers): self.workers = max_workers
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def submit(self, *args):
                future = Future()
                submitted.append(future)
                return future
        def wait(futures, **kwargs):
            self.assertLessEqual(len(futures), 100)
            scan._scan_stop.set()
            return set(), set(futures)
        try:
            scan._scan_results = []
            scan._scan_stop.clear()
            with patch.object(scan, '_find_nic', return_value=(True, nic)), patch.object(scan, 'ThreadPoolExecutor', Executor), patch.object(scan, 'wait', side_effect=wait):
                scan._scan_worker_impl('Ethernet', '10.0.0.0/16', True)
            self.assertEqual(len(submitted), 100)
            self.assertTrue(all(future.cancelled() for future in submitted))
            self.assertFalse(scan._last_scan_context['completed'])
        finally:
            for name, value in saved.items(): setattr(scan, name, value)
            scan._last_scan_context.update(context)
            scan._scan_stop.clear()
