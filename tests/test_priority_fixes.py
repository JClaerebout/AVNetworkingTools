import copy
import threading
import unittest
from unittest.mock import Mock, patch

import connection_utils as connection
import nic_utils as nic
import scan_utils as scan
import script_utils as scripts
from app import create_app


class RequestProtectionTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.token = self.app.config["ACTION_TOKEN"]

    @patch("routes.set_dhcp", return_value=(True, "Applied"))
    def test_forms_require_token_and_accept_valid_token(self, change):
        for token in (None, "invalid"):
            data = {"interface": "Offline", "mode": "dhcp"}
            if token: data["_action_token"] = token
            self.assertEqual(self.client.post("/apply", data=data).status_code, 403)
        change.assert_not_called()
        self.assertEqual(self.client.post("/apply", data={"interface":"Offline", "mode":"dhcp", "_action_token":self.token}).status_code, 302)
        change.assert_called_once()

    @patch("routes.run_command", return_value={"success":True})
    def test_json_action_and_origin_validation(self, run):
        headers = {"X-AV-Token": self.token, "Origin":"http://localhost"}
        self.assertEqual(self.client.post("/command-line/run",json={"command":"mock"},headers=headers).status_code,200)
        for origin in ("https://untrusted.invalid", "null", "http://localhost:4444"):
            headers["Origin"] = origin
            self.assertEqual(self.client.post("/command-line/run",json={},headers=headers).status_code,403)
        self.assertEqual(run.call_count,1)

    def test_update_delete_and_beacon_routes_protected(self):
        for method, path in (("POST","/api/update/install"),("DELETE","/scripts/saved/test"),("POST","/multicast/stop")):
            self.assertEqual(self.client.open(path,method=method).status_code,403)
        with patch("routes.stop_multicast_test", return_value=(True,"Stopped")), patch("routes.get_multicast_status",return_value={}):
            self.assertEqual(self.client.post("/multicast/stop",data={"_action_token":self.token}).status_code,200)

    def test_host_cross_site_and_per_instance_secret(self):
        self.assertEqual(self.client.get("/converter",headers={"Host":"attacker.invalid"}).status_code,403)
        self.assertEqual(self.client.get("/converter",headers={"Sec-Fetch-Site":"cross-site"}).status_code,403)
        self.assertNotEqual(self.token,create_app().config["ACTION_TOKEN"])
        response = self.client.get("/converter")
        self.assertIn(self.token.encode(),response.data)
        self.assertEqual(response.headers["X-Frame-Options"],"DENY")

    @patch("routes.get_nics",return_value=[{"name":"Offline","if_index":1,"dns":[],"status":"static"}])
    def test_native_forms_render_tokens(self, _nics):
        response = self.client.get("/")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data.count(b'name="_action_token"'),5)


class ScanFixTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k:copy.deepcopy(getattr(scan,k)) for k in ("_scan_results","_last_scan_context","_scan_running","_lookup_running","_monitor_log")}
        scan._scan_results = []; scan._scan_stop.clear()
        scan._scan_running = True; scan._lookup_running = False
        self.nic = {"ip":"10.0.0.1","mac":"AA:BB:CC:DD:EE:FF","network":"10.0.0.0/16"}

    def tearDown(self):
        for k,v in self.saved.items(): setattr(scan,k,v)
        scan._scan_stop.clear()

    def test_oversize_and_ipv6_rejected_before_enumeration(self):
        with patch.object(scan.ipaddress.IPv4Network,"hosts",side_effect=AssertionError("Must not enumerate")), patch.object(scan,"_discover_host") as discover:
            for network in ("10.0.0.0/21","10.0.0.0/8","::/0"):
                self.assertFalse(scan.start_scan("Offline",network,True)[0])
            discover.assert_not_called()

    def test_default_large_adapter_range_rejected_and_state_reset(self):
        with patch.object(scan,"_find_nic",return_value=(True,self.nic)), patch.object(scan,"_discover_host") as discover:
            scan._scan_worker("Offline")
        self.assertFalse(scan._scan_running)
        self.assertIn("at most 1024",scan._scan_message)
        discover.assert_not_called()

    def test_interface_failure_always_resets_state(self):
        with patch.object(scan,"_find_nic",side_effect=RuntimeError("WMI unavailable")):
            scan._scan_worker("Offline")
        self.assertFalse(scan._scan_running)
        self.assertIn("WMI unavailable",scan._scan_message)

    def test_scan_thread_start_failure_resets_state(self):
        scan._scan_running=False
        thread=Mock();thread.start.side_effect=RuntimeError("No thread")
        with patch.object(scan.threading,"Thread",return_value=thread):
            self.assertFalse(scan.start_scan("Offline","192.0.2.0/30",True)[0])
        self.assertFalse(scan._scan_running)

    def test_small_custom_range_on_large_adapter_is_allowed(self):
        with patch.object(scan,"_find_nic",return_value=(True,self.nic)),patch.object(scan,"_discover_host",return_value=(False,"")) as discover:
            scan._scan_worker("Offline","10.0.0.0/30",True)
        self.assertTrue(scan._last_scan_context["completed"])
        self.assertEqual(discover.call_count,1)

    def test_routed_targets_without_mac_still_appear(self):
        with patch.object(scan,"_find_nic",return_value=(True,self.nic)), patch.object(scan,"_discover_host",return_value=(True,"")):
            scan._scan_worker("Offline","192.0.2.0/30",True)
        self.assertEqual([r["ip"] for r in scan._scan_results],["192.0.2.1","192.0.2.2"])
        self.assertEqual([r["mac"] for r in scan._scan_results],["",""])
        self.assertTrue(scan._last_scan_context["completed"])

    def test_monitor_accepts_reachable_without_mac(self):
        with patch.object(scan,"_discover_host",return_value=(True,"")):
            result = scan._monitor_probe_round("10.0.0.1",["192.0.2.1"],scan.ipaddress.ip_network("10.0.0.0/24"),threading.Event())
        self.assertEqual(result,{"192.0.2.1":""})

    def test_mac_replacement_and_recent_alternation_expiry(self):
        scan._monitor_update_device("192.0.2.1","AA",True)
        with patch.object(scan.time,"monotonic",return_value=100):
            scan._monitor_update_device("192.0.2.1","BB",True)
        self.assertFalse(scan._scan_results[0]["duplicate_ip"])
        with patch.object(scan.time,"monotonic",return_value=105):
            scan._monitor_update_device("192.0.2.1","AA",True)
        self.assertTrue(scan._scan_results[0]["duplicate_ip"])
        with patch.object(scan.time,"monotonic",return_value=140):
            scan._monitor_update_device("192.0.2.1","AA",True)
        self.assertFalse(scan._scan_results[0]["duplicate_ip"])
        self.assertEqual(len(scan._scan_results[0]["mac_history"]),2)

    def test_route_preserves_action_failure_message(self):
        app=create_app(); client=app.test_client()
        with patch("routes.start_scan",return_value=(False,"Choose an interface")),patch("routes.get_scan_status",return_value={"message":"Idle"}):
            response=client.post("/ip-scan/start",json={},headers={"X-AV-Token":app.config["ACTION_TOKEN"]})
        self.assertEqual(response.get_json()["message"],"Choose an interface")


class ConnectionFixTests(unittest.TestCase):
    def setUp(self):
        self.saved = connection._session
        connection._session = None

    def tearDown(self):
        connection.stop_connection()
        connection._session = self.saved

    def session(self, protocol="tcp"):
        s=connection._Session(protocol,"mock")
        s.state="Connected"; s.transport=Mock(); s.resources=[s.transport]
        connection._session=s
        return s

    def test_reset_and_eof_close_resources_and_update_status(self):
        for data in (OSError("reset"), b""):
            s=self.session()
            if isinstance(data,Exception): s.transport.recv.side_effect=data
            else: s.transport.recv.return_value=data
            connection._reader(s)
            self.assertFalse(connection.get_connection_status()["running"])
            s.transport.close.assert_called_once()

    def test_stop_during_connect_closes_late_resource_without_touching_new_session(self):
        entered=threading.Event(); release=threading.Event(); transport=Mock()
        def connect(*args,**kwargs):
            entered.set(); release.wait(2); return transport
        result=[]
        with patch.object(connection.socket,"create_connection",side_effect=connect):
            thread=threading.Thread(target=lambda: result.append(connection.start_connection("tcp","mock",23)))
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertEqual(connection.get_connection_status()["state"],"Connecting")
            self.assertFalse(connection.start_connection("tcp","mock",23)[0])
            self.assertTrue(connection.stop_connection()[0])
            new=self.session()
            release.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0][0])
        transport.close.assert_called_once()
        self.assertIs(connection._session,new)
        self.assertEqual(new.state,"Connected")

    def test_late_old_reader_cannot_close_new_session(self):
        old=self.session(); new=self.session()
        old.transport.recv.side_effect=OSError("late reset")
        connection._reader(old)
        self.assertEqual(connection.get_connection_status()["state"],"Connected")
        new.transport.close.assert_not_called()

    def test_ssh_uses_complete_send_and_reports_timeout(self):
        s=self.session("ssh")
        s.transport.send.return_value=1
        self.assertTrue(connection.send_data("ABCDE")[0])
        s.transport.send.assert_not_called()
        s.transport.sendall.assert_called_once_with(b"ABCDE")
        s.transport.sendall.side_effect=TimeoutError("timeout")
        success,message=connection.send_data("ABCDE")
        self.assertFalse(success); self.assertIn("partial",message)
        self.assertEqual(connection.get_connection_status()["state"],"Failed")

    def test_serial_rx_retains_original_bytes(self):
        s=self.session("rs232")
        s.transport.read.side_effect=[b"\xff\x00",OSError("closed")]
        connection._reader(s)
        received=[r for r in connection.get_connection_status()["output"] if isinstance(r,dict) and r["direction"]=="RX"]
        self.assertEqual(received[-1]["hex"],"FF 00")

    def test_partial_serial_write_fails(self):
        s=self.session("rs232"); s.transport.write.return_value=1
        self.assertFalse(connection.send_data("ABCDE")[0])

    def test_ssh_connect_failure_closes_client(self):
        client=Mock(); client.connect.side_effect=OSError("auth failed")
        with patch.object(connection.paramiko,"SSHClient",return_value=client):
            self.assertFalse(connection.start_connection("ssh","mock",22,"user")[0])
        client.close.assert_called_once()

    def test_ssh_eof_and_udp_error_cleanup(self):
        ssh=self.session("ssh")
        ssh.transport.recv_ready.return_value=True
        ssh.transport.recv.return_value=b""
        connection._reader(ssh)
        self.assertEqual(ssh.state,"Disconnected")
        udp=self.session("udp")
        udp.transport.recv.side_effect=OSError("error")
        connection._reader(udp)
        self.assertEqual(udp.state,"Failed")
        udp.transport.close.assert_called_once()

    def test_immediate_remote_close_cannot_restore_connected_state(self):
        class ImmediateThread:
            def __init__(self,target,args,**kwargs): self.target=target;self.args=args
            def start(self): self.target(*self.args)
        sock=Mock();sock.recv.return_value=b""
        with patch.object(connection.socket,"create_connection",return_value=sock),patch.object(connection.threading,"Thread",ImmediateThread):
            connection.start_connection("tcp","mock",23)
        self.assertFalse(connection.get_connection_status()["running"])

    def test_reader_thread_start_failure_closes_transport(self):
        sock=Mock();thread=Mock();thread.start.side_effect=RuntimeError("no thread")
        with patch.object(connection.socket,"create_connection",return_value=sock),patch.object(connection.threading,"Thread",return_value=thread):
            self.assertFalse(connection.start_connection("tcp","mock",23)[0])
        sock.close.assert_called_once()
        self.assertEqual(connection.get_connection_status()["state"],"Failed")


class NetworkSettingsTests(unittest.TestCase):
    def test_invalid_settings_never_mutate_windows(self):
        values=[("bad","255.255.255.0","",[]),("192.0.2.10","bad","",[]),
                ("192.0.2.10","255.255.255.0","198.51.100.1",[]),
                ("192.0.2.10","255.255.255.0","",["not-dns"]),
                ("192.0.2.0","255.255.255.0","",[])]
        with patch.object(nic,"is_interface_connected",return_value=True),patch.object(nic,"run_cmd") as run:
            for args in values: self.assertFalse(nic.set_static("Offline",*args)[0])
        run.assert_not_called()

    def test_verification_unavailable_not_reported_as_clean(self):
        with patch.object(nic,"is_interface_connected",return_value=True),patch.object(nic,"_remember_config"),patch.object(nic,"run_cmd",return_value=(0,"","")),patch.object(nic,"run_powershell",return_value=(1,"","failure")),patch.object(nic,"save_history_entry"),patch.object(nic.time,"sleep"):
            success,message=nic.set_static("Offline","192.0.2.10","255.255.255.0","",[])
        self.assertTrue(success);self.assertIn("verification unavailable",message)
        self.assertNotIn("No IP conflict detected",message)

    def test_powershell_interface_is_literal(self):
        with patch.object(nic,"run_powershell",return_value=(0,"Preferred","")) as run:
            nic.check_windows_ip_duplicate("Room's $Adapter", "192.0.2.10")
        self.assertIn("'Room''s $Adapter'",run.call_args.args[0])

    def test_restore_uses_original_dhcp_or_static_settings(self):
        with patch.dict(nic._previous_configs,{"Offline":{"dhcp_raw":"Enabled"}}),patch.object(nic,"set_dhcp",return_value=(True,"Restored")) as restore:
            self.assertTrue(nic.restore_previous_config("Offline")[0]);restore.assert_called_once_with("Offline")
        with patch.dict(nic._previous_configs,{"Offline":{"ip":"192.0.2.10","subnet":"255.255.255.0","dns":["192.0.2.1"]}}),patch.object(nic,"set_static",return_value=(True,"Restored")) as restore:
            self.assertTrue(nic.restore_previous_config("Offline")[0]);restore.assert_called_once_with("Offline","192.0.2.10","255.255.255.0","",["192.0.2.1"])

    def test_failed_snapshot_read_removes_stale_restore(self):
        with patch.dict(nic._previous_configs,{"Offline":{"ip":"old"}}),patch.object(nic,"get_nics",side_effect=RuntimeError("unavailable")):
            nic._remember_config("Offline")
            self.assertFalse(nic.restore_previous_config("Offline")[0])


class ScriptOutcomeTests(unittest.TestCase):
    def run_script(self, connection_result):
        scripts._stop.clear();scripts._pause.clear()
        blocks=scripts._normalize_blocks([{"type":"target","targets":"mock","port":23},{"type":"command","value":"STATUS"}])
        with patch.object(scripts,"_connect_one",return_value=connection_result),patch.object(scripts,"_wait_for_final_responses"):
            scripts._run_script(blocks)
        return scripts.get_script_status()

    def test_all_targets_failed_is_failed(self):
        status=self.run_script(None)
        self.assertEqual(status["status_text"],"Failed")
        self.assertEqual(status["outcomes"]["targets_failed"],1)
        self.assertEqual(status["outcomes"]["commands_skipped"],1)

    def test_failed_send_is_failed(self):
        target=Mock();target.send.side_effect=OSError("failed")
        self.assertEqual(self.run_script(target)["status_text"],"Failed")

    def test_partial_targets_report_completed_with_errors(self):
        scripts._stop.clear();scripts._pause.clear()
        blocks=scripts._normalize_blocks([{"type":"target","targets":"one\ntwo","port":23},{"type":"command","value":"STATUS"}])
        with patch.object(scripts,"_connect_one",side_effect=[Mock(),None]),patch.object(scripts,"_wait_for_final_responses"):
            scripts._run_script(blocks)
        self.assertEqual(scripts.get_script_status()["status_text"],"Completed with errors")

    def test_invalid_command_rejected_before_execution(self):
        with self.assertRaises(ValueError):
            scripts._normalize_blocks([{"type":"target","targets":"mock","port":23},{"type":"command","is_hex":True,"value":"GG"}])
