import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import Mock, patch

import api_tools as api


class APIIntegrationTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('Worker did not finish')

    def tearDown(self):
        api.stop_ws()
        self.wait_for(lambda: api.status()['ws_state'] == 'Disconnected')
        api.clear()

    def test_tls_context_preserves_verification_and_platform_trust(self):
        with patch('api_tools.sys.platform', 'darwin'), patch.dict('sys.modules', {'certifi': Mock(where=lambda: '/bundle/cacert.pem')}), patch('api_tools.ssl.create_default_context') as create:
            context = api.tls_context()
            self.assertIs(context, create.return_value)
            context.load_verify_locations.assert_called_once_with(cafile='/bundle/cacert.pem')
        system_context = ssl.create_default_context()
        with patch('api_tools.sys.platform', 'win32'), patch('api_tools.ssl.create_default_context', return_value=system_context):
            context = api.tls_context()
            self.assertIs(context, system_context)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
        context = api.tls_context(False)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        self.assertFalse(context.check_hostname)

    def test_http_error_body_and_redirect_are_displayed(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302 if self.path == '/redirect' else 401)
                self.send_header('Location', '/must-not-follow')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"message":"test"}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for path, code in [('/', 401), ('/redirect', 302)]:
                api.start_http({'url': f'http://127.0.0.1:{server.server_port}{path}'})
                self.wait_for(lambda: not api.status()['http_running'])
                result = api.status()['result']
                self.assertEqual(result['status'], code)
                self.assertIn('test', result['body'])
        finally:
            server.shutdown(); server.server_close()

    def test_real_websocket_text_send_receive_and_disconnect(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen()
        def server():
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(3)
                data = b''
                while b'\r\n\r\n' not in data:
                    data += conn.recv(4096)
                key = next(line.split(b':', 1)[1].strip() for line in data.split(b'\r\n') if line.lower().startswith(b'sec-websocket-key:'))
                accept = base64.b64encode(hashlib.sha1(key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
                conn.recv(4096)
                conn.sendall(b'\x81\x05hello')
                try: conn.recv(4096)
                except OSError: pass
        thread = threading.Thread(target=server, daemon=True); thread.start()
        try:
            api.start_ws({'url': f'ws://127.0.0.1:{listener.getsockname()[1]}'})
            self.wait_for(lambda: api.status()['ws_state'] == 'Connected')
            api.send_ws({'text': 'hello'})
            self.wait_for(lambda: any(e['direction'] == 'Received' and e['text'] == 'hello' for e in api.status()['events']))
            api.stop_ws()
            self.wait_for(lambda: api.status()['ws_state'] == 'Disconnected')
        finally:
            listener.close(); thread.join(4)

    def test_validation_and_secret_free_setup(self):
        for data in [{'url': 'file:///tmp/a'}, {'url': 'http://user:password@localhost'},
                     {'url': 'http://localhost', 'headers': 'Host: other'},
                     {'url': 'http://localhost', 'timeout': 0}]:
            with self.assertRaises(ValueError): api.settings(data)
        with patch('workspace_state.read_section', return_value={}), patch('workspace_state.write_section') as write:
            api.saved_setups({'name': 'test', 'url': 'https://localhost/path?key=secret', 'password': 'secret', 'headers': 'X-Key: secret', 'body': 'secret'})
            self.assertNotIn('secret', str(write.call_args))
            self.assertEqual(write.call_args.args[1]['test']['origin'], 'https://localhost')

    def test_routes_and_native_export(self):
        from app import create_app
        from tests.http_test_support import authorized_client
        client = authorized_client(create_app())
        self.assertEqual(client.get('/api-tools').status_code, 200)
        self.assertEqual(client.post('/api-tools/http', json=[]).status_code, 400)
        with patch('export_utils.save_export') as save:
            self.assertEqual(client.post('/api-tools/export', json={'text': 'result'}).status_code, 200)
            save.assert_called_once_with('api-results.txt', 'result')
