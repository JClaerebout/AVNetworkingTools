const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map(), listeners = {};
const values = new Map();
const key = 'avNetworkingTools:page-fields:api-modes';
values.set(key, JSON.stringify({activeMode: 'ws', http: {url: 'https://http-device', method: 'POST', timeout: 12}, ws: {url: 'wss://ws-device', timeout: 20, verify: false}}));
function element(id) {
    if (!elements.has(id)) elements.set(id, {value: '', checked: true, handlers: {}, dataset: {},
        addEventListener(name, fn) { this.handlers[name] = fn; },
        dispatchEvent(event) { this.handlers[event.type]?.(event); },
        replaceChildren() {}, add() {}});
    return elements.get(id);
}
const tabs = ['http', 'ws'].map(mode => ({...element(mode), dataset: {mode}, setAttribute() {}, classList: {toggle() {}}}));
const context = {
    window: {workspaceStorage: {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value)}},
    document: {getElementById: element, querySelectorAll: () => tabs, addEventListener: (name, fn) => { listeners[name] = fn; }},
    Event: class { constructor(type) { this.type = type; } }, URL,
    fetch: async () => ({ok: true, json: async () => ({setups: {}, ws_state: 'Disconnected'})}),
    Option: class {}, setInterval() {}
};
vm.runInNewContext(fs.readFileSync('static/api_tools.js', 'utf8'), context);
listeners.DOMContentLoaded();
assert.equal(element('apiHeading').textContent, 'WebSocket');
assert.equal(element('apiImport').dataset.workspaceImport, 'ws');
assert.equal(element('apiUrl').value, 'wss://ws-device');
assert.equal(element('apiVerify').checked, false);
element('apiUrl').value = 'wss://user:password@ws-device/private?token=secret';
element('apiPassword').value = 'private-password';
element('apiBody').value = 'private-body';
element('apiTool').handlers.input();
const saved = JSON.parse(values.get(key));
assert.equal(saved.ws.url, 'wss://ws-device');
assert.equal(saved.http.method, 'POST');
assert.ok(!values.get(key).includes('private'));
tabs[0].handlers.click();
assert.equal(element('apiImport').dataset.workspaceImport, 'http');
assert.equal(element('apiUrl').value, 'https://http-device');
assert.equal(element('apiMethod').value, 'POST');
tabs[1].handlers.click();
assert.equal(element('apiBody').value, 'private-body');
assert.equal(JSON.parse(values.get(key)).activeMode, 'ws');
console.log('API workspace protocol restoration and credential exclusion passed.');
