"""Isolated HTTP/WebSocket tester. Network contents are session-only."""
import base64
from collections import deque
from datetime import datetime
import json
import re
import ssl
import sys
import threading
import time
from urllib import request, error
from urllib.parse import urlsplit

LIMIT = 1024 * 1024
_lock = threading.RLock()
_session = None
_http_running = False
_result = None
_events = deque(maxlen=200)


def tls_context(verify=True):
    """Use system trust, supplemented by the packaged CA bundle on macOS."""
    if not verify:
        return ssl._create_unverified_context()
    context = ssl.create_default_context()
    if sys.platform == 'darwin':
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    return context


def settings(data, websocket=False):
    url = data.get('url', '')
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('Enter a valid URL.')
    parsed = urlsplit(url)
    if parsed.scheme not in ({'ws', 'wss'} if websocket else {'http', 'https'}) or not parsed.hostname:
        raise ValueError('Enter a complete URL with the correct scheme.')
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError('Use the authentication fields; URL credentials and fragments are not supported.')
    parsed.port  # Validate the port.
    timeout = float(data.get('timeout', 10))
    if not 1 <= timeout <= 60:
        raise ValueError('Timeout must be between 1 and 60 seconds.')
    headers = {}
    raw = data.get('headers', '')
    if not isinstance(raw, str) or len(raw) > 32768:
        raise ValueError('Headers are too large.')
    for line in raw.splitlines():
        if not line.strip():
            continue
        name, sep, value = line.partition(':')
        if not sep or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name.strip()) or '\r' in value:
            raise ValueError('Enter one header per line: Name: value.')
        if name.lower().strip() in {'host', 'content-length', 'transfer-encoding', 'connection', 'upgrade'} or name.lower().startswith('sec-websocket-'):
            raise ValueError('Transport headers are managed automatically.')
        headers[name.strip()] = value.strip()
    auth = data.get('auth', 'none')
    if auth == 'basic':
        credential = f"{data.get('username', '')}:{data.get('password', '')}"
        headers['Authorization'] = 'Basic ' + base64.b64encode(credential.encode()).decode()
    elif auth == 'bearer':
        headers['Authorization'] = 'Bearer ' + str(data.get('token', ''))
    elif auth != 'none':
        raise ValueError('Unknown authentication method.')
    if any('\r' in value or '\n' in value for value in headers.values()):
        raise ValueError('Header values cannot contain newlines.')
    verify = data.get('verify', True)
    if not isinstance(verify, bool):
        raise ValueError('Invalid TLS setting.')
    return url, headers, timeout, verify


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Display redirects; never forward credentials to another host.


def start_http(data):
    global _http_running, _result
    url, headers, timeout, verify = settings(data)
    method = data.get('method', 'GET')
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
        raise ValueError('Unsupported HTTP method.')
    body = data.get('body', '')
    if not isinstance(body, str) or len(body.encode()) > LIMIT:
        raise ValueError('Request body must be at most 1 MiB.')
    with _lock:
        if _http_running:
            raise ValueError('An HTTP request is already running.')
        _http_running = True
        _result = None
    def run():
        global _http_running, _result
        started = time.monotonic()
        try:
            context = tls_context(verify)
            opener = request.build_opener(request.ProxyHandler({}), NoRedirect(), request.HTTPSHandler(context=context))
            req = request.Request(url, data=body.encode() if body else None, headers=headers, method=method)
            try:
                response = opener.open(req, timeout=timeout)
            except error.HTTPError as exc:
                response = exc
            with response:
                # Bounded size and total read deadline, including trickling responses.
                chunks = bytearray()
                while len(chunks) <= LIMIT:
                    if time.monotonic() - started > timeout:
                        raise TimeoutError('Response exceeded the request deadline.')
                    chunk = response.read1(min(65536, LIMIT + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                text = bytes(chunks[:LIMIT]).decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                result = dict(status=response.status, reason=response.reason,
                              headers=list(response.headers.items()), body=text,
                              truncated=len(chunks) > LIMIT)
        except Exception:
            result = dict(error='Request failed. Check the address, connection, authentication and TLS settings.')
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        with _lock:
            _result = result
            _http_running = False
    threading.Thread(target=run, daemon=True, name='api-http').start()


def event(direction, text):
    with _lock:
        _events.append({'time': datetime.now().astimezone().isoformat(timespec='seconds'),
                        'direction': direction, 'text': text[:65536]})


def start_ws(data):
    global _session
    import websocket
    url, headers, timeout, verify = settings(data, websocket=True)
    session = {'socket': None, 'stop': threading.Event(), 'state': 'Connecting'}
    with _lock:
        if _session is not None:
            raise ValueError('Disconnect the active WebSocket first.')
        _session = session
    def run():
        global _session
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=timeout, header=headers,
                http_no_proxy=['*'], redirect_limit=0,
                sslopt={'context': tls_context(verify),
                        'cert_reqs': ssl.CERT_REQUIRED if verify else ssl.CERT_NONE,
                        'check_hostname': verify})
            with _lock:
                session['socket'] = ws
                session['state'] = 'Connected'
            event('Status', 'Connected')
            while not session['stop'].is_set():
                try:
                    opcode, message = ws.recv_data()
                except websocket.WebSocketTimeoutException:
                    continue
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    break
                if opcode == websocket.ABNF.OPCODE_TEXT:
                    event('Received', message.decode('utf-8') if isinstance(message, bytes) else message)
                elif opcode == websocket.ABNF.OPCODE_BINARY:
                    event('Received', f'[Binary message: {len(message)} bytes; display unsupported]')
        except Exception:
            if not session['stop'].is_set():
                event('Error', 'WebSocket failed. Check address, handshake headers, authentication and TLS settings.')
        finally:
            if ws:
                ws.close(timeout=1)
            with _lock:
                if _session is session:
                    _session = None
            event('Status', 'Disconnected')
    threading.Thread(target=run, daemon=True, name='api-websocket').start()


def stop_ws():
    with _lock:
        session = _session
        if session:
            session['stop'].set()
            session['state'] = 'Stopping'
            ws = session['socket']
        else:
            ws = None
    if ws:
        try:
            ws.abort()
        except OSError:
            pass


def send_ws(data):
    text = data.get('text', '')
    if not isinstance(text, str) or len(text.encode()) > LIMIT:
        raise ValueError('Message must be at most 1 MiB.')
    with _lock:
        ws = _session.get('socket') if _session and _session['state'] == 'Connected' else None
    if not ws:
        raise ValueError('Connect a WebSocket first.')
    try:
        ws.send(text)
    except Exception:
        raise ValueError('Could not send; the WebSocket may have closed.') from None
    event('Sent', text)


def status():
    with _lock:
        return dict(http_running=_http_running, result=_result,
                    ws_state=_session['state'] if _session else 'Disconnected', events=list(_events))


def clear(mode=None):
    global _result
    with _lock:
        if mode != 'ws':
            _result = None
        if mode != 'http':
            _events.clear()


def saved_setups(data=None):
    from workspace_state import read_section, write_section
    if data is None:
        return read_section('api_setups') or {}
    name = data.get('name', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError('Enter a setup name (1–100 characters).')
    # Explicit allowlist. URLs/bodies/headers can all embed secrets, so never persist them.
    setup = {key: data[key] for key in ('mode', 'method', 'timeout', 'verify', 'auth') if key in data}
    if not data.get('delete'):
        url, _, _, _ = settings(data, websocket=data.get('mode') == 'ws')
        parsed = urlsplit(url)
        setup['origin'] = parsed.scheme + '://' + parsed.netloc
    setups = saved_setups()
    if data.get('delete'):
        setups.pop(name.strip(), None)
    else:
        setups[name.strip()] = setup
    write_section('api_setups', setups)
    return setups
