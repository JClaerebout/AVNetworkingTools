(() => {
    const el = id => document.getElementById(id);
    let mode = 'http', setups = {}, current = {}, polling = false;
    const modeDrafts = {};
    function showMode(value) {
        if (value !== mode) {
            modeDrafts[mode] = config();
            const draft = modeDrafts[value] || {};
            const defaults = {method: 'GET', auth: 'none', timeout: 10};
            for (const name of ['url', 'method', 'headers', 'auth', 'username', 'password', 'token', 'timeout', 'body']) {
                el(`api${name[0].toUpperCase()}${name.slice(1)}`).value = draft[name] ?? defaults[name] ?? '';
            }
            el('apiVerify').checked = draft.verify !== false;
            el('apiAuth').dispatchEvent(new Event('change'));
            el('apiVerify').dispatchEvent(new Event('change'));
        }
        mode = value;
        document.querySelectorAll('[data-mode]').forEach(button => {
            button.setAttribute('aria-selected', String(button.dataset.mode === mode));
            button.classList.toggle('secondary', button.dataset.mode !== mode);
        });
        el('httpOptions').hidden = mode !== 'http';
        el('apiConnect').hidden = el('apiDisconnect').hidden = mode !== 'ws';
        el('apiSend').textContent = mode === 'http' ? 'Send request' : 'Send message';
        el('bodyLabel').textContent = mode === 'http' ? 'Request body (optional text or JSON)' : 'Message (text or JSON)';
        el('apiHeading').textContent = mode === 'http' ? 'HTTP(S) Requests' : 'WebSocket';
        el('apiDescription').textContent = mode === 'http'
            ? 'Send an individual HTTP request and inspect its response.'
            : 'Connect once, then send and receive text messages. The connection stays open across page navigation.';
        el('apiUrl').placeholder = mode === 'http' ? 'https://192.168.1.100/api/status' : 'wss://echo.websocket.org';
        el('apiResultHeading').textContent = mode === 'http' ? 'HTTP response' : 'WebSocket messages';
        el('apiResultHint').textContent = mode === 'http'
            ? 'Redirects are displayed without following them. Responses are limited to 1 MiB. Results stay in memory until cleared or the app exits.'
            : 'The log retains 200 messages with up to 64 KiB displayed per message. Messages stay in memory until cleared or the app exits.';
        updateButtons();
        renderOutput();
    }
    function config() {
        return {mode, url: el('apiUrl').value.trim(), method: el('apiMethod').value,
            headers: el('apiHeaders').value, auth: el('apiAuth').value,
            username: el('apiUsername').value, password: el('apiPassword').value,
            token: el('apiToken').value, timeout: Number(el('apiTimeout').value),
            verify: el('apiVerify').checked, body: el('apiBody').value, text: el('apiBody').value};
    }
    async function call(path, data) {
        const response = await fetch(`/api-tools/${path}`, data === undefined ? {} : {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || 'Action failed.');
        return result;
    }
    function updateButtons() {
        el('apiSend').disabled = mode === 'http' ? !!current.http_running : current.ws_state !== 'Connected';
        el('apiConnect').disabled = current.ws_state !== 'Disconnected';
        el('apiDisconnect').disabled = current.ws_state === 'Disconnected';
    }
    function pretty(text) { try { return JSON.stringify(JSON.parse(text), null, 2); } catch (_) { return text; } }
    function renderOutput() {
        const result = current.result;
        const http = result ? result.error || `${result.status} ${result.reason} · ${result.elapsed_ms} ms\n${result.headers.map(pair => pair.join(': ')).join('\n')}\n\n${pretty(result.body)}${result.truncated ? '\n[Response truncated]' : ''}` : '';
        const messages = (current.events || []).map(item => `${item.time} ${item.direction}\n${pretty(item.text)}`).join('\n\n');
        el('apiOutput').textContent = (mode === 'http' ? http : messages) || 'No results yet.';
        el('apiState').textContent = mode === 'http'
            ? (current.http_running ? 'HTTP request running…' : 'Ready to send an HTTP request.')
            : `WebSocket: ${current.ws_state || 'Disconnected'}`;
    }
    async function refresh() {
        if (polling) return;
        polling = true;
        try {
            current = await call('status');
            updateButtons();
            renderOutput();
        } catch (error) { el('apiState').textContent = error.message; }
        finally { polling = false; }
    }
    async function act(action, data = config()) {
        try { await call(action, data); await refresh(); }
        catch (error) { el('apiState').textContent = error.message; }
    }
    document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => showMode(button.dataset.mode)));
    el('apiAuth').addEventListener('change', () => {
        el('basicAuth').hidden = el('apiAuth').value !== 'basic';
        el('bearerAuth').hidden = el('apiAuth').value !== 'bearer';
    });
    el('apiVerify').addEventListener('change', () => { el('tlsWarning').hidden = el('apiVerify').checked; });
    el('apiSend').addEventListener('click', () => act(mode === 'http' ? 'http' : 'send'));
    el('apiConnect').addEventListener('click', () => act('connect'));
    el('apiDisconnect').addEventListener('click', () => act('disconnect', {}));
    el('apiClear').addEventListener('click', () => act('clear', {mode}));
    el('apiCopy').addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(el('apiOutput').textContent); }
        catch (_) { el('apiState').textContent = 'Clipboard unavailable. Select and copy the output manually.'; }
    });
    el('apiExport').addEventListener('click', async () => {
        try { await call('export', {text: el('apiOutput').textContent}); }
        catch (error) { el('apiState').textContent = error.message; }
    });
    function list(data) {
        setups = data.setups;
        el('apiSetup').replaceChildren(new Option('Select a setup', ''));
        Object.keys(setups).sort().forEach(name => el('apiSetup').add(new Option(name, name)));
    }
    el('apiSave').addEventListener('click', async () => {
        const name = el('apiSetupName').value.trim();
        if (Object.hasOwn(setups, name) && !confirm(`Replace setup “${name}”?`)) return;
        try { list(await call('setups', {...config(), name})); el('apiState').textContent = 'Setup saved without request contents or credentials.'; }
        catch (error) { el('apiState').textContent = error.message; }
    });
    el('apiLoad').addEventListener('click', () => {
        const setup = setups[el('apiSetup').value];
        if (!setup) return;
        showMode(setup.mode || 'http');
        el('apiSetupName').value = el('apiSetup').value;
        ['Headers', 'Username', 'Password', 'Token', 'Body'].forEach(name => { el(`api${name}`).value = ''; });
        el('apiUrl').value = setup.origin || '';
        el('apiMethod').value = setup.method || 'GET';
        el('apiAuth').value = setup.auth || 'none';
        el('apiTimeout').value = setup.timeout || 10;
        el('apiVerify').checked = setup.verify !== false;
        el('apiAuth').dispatchEvent(new Event('change'));
        el('apiVerify').dispatchEvent(new Event('change'));
    });
    el('apiDelete').addEventListener('click', async () => {
        const name = el('apiSetup').value;
        if (!name || !confirm(`Delete setup “${name}”?`)) return;
        try { list(await call('setups', {name, delete: true})); }
        catch (error) { el('apiState').textContent = error.message; }
    });
    call('setups').then(list).catch(error => { el('apiState').textContent = error.message; });
    // Global draft restoration runs on DOMContentLoaded; reflect restored settings afterwards.
    document.addEventListener('DOMContentLoaded', () => {
        el('apiAuth').dispatchEvent(new Event('change'));
        el('apiVerify').dispatchEvent(new Event('change'));
    });
    showMode('http'); refresh(); setInterval(refresh, 1000);
})();
