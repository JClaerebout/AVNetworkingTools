const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map(), values = new Map();
const key = 'avNetworkingTools:scriptDraft:v1';
function el(id) {
    if (!elements.has(id)) elements.set(id, {
        handlers: {}, style: {}, classList: {add() {}, remove() {}},
        addEventListener(name, fn) { this.handlers[name] = fn; },
        querySelectorAll() { return []; },
        showModal() { this.open = true; },
        close() { this.open = false; this.handlers.close?.(); }
    });
    return elements.get(id);
}
const windowMock = {
    workspaceStorage: {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)},
    addEventListener() {}, location: {assign() { throw new Error('Must stay on IP Scan'); }}
};
const source = fs.readFileSync('static/ip_scan.js', 'utf8').replace(
    '    refreshScanStatus();\n    setInterval(refreshScanStatus, 1000);',
    '    window.selectDevice = ip => { selectedContextItem = {ip}; };'
);
vm.runInNewContext(source, {
    window: windowMock, document: {currentScript: {dataset: {}}, getElementById: el, addEventListener() {}},
    setTimeout() {}, clearTimeout() {}
});
const read = () => JSON.parse(values.get(key));
function send(ip) { windowMock.selectDevice(ip); el('addToScriptButton').handlers.click(); }
send('192.0.2.1');
assert.equal(read()[0].targets, '192.0.2.1');
assert.equal(read()[1].type, 'command');
assert.ok(!el('scanScriptDialog').open);
send('192.0.2.2');
assert.equal(el('scanScriptDialog').open, true);
assert.equal(read()[0].targets, '192.0.2.1');
el('scanScriptAppend').handlers.click();
assert.equal(read()[0].targets, '192.0.2.1\n192.0.2.2');
send('192.0.2.3'); el('scanScriptNew').handlers.click();
assert.equal(read()[2].targets, '192.0.2.3');
assert.equal(read()[2].port, '23');
const before = values.get(key);
send('192.0.2.4'); el('scanScriptCancel').handlers.click();
assert.equal(values.get(key), before);
send('192.0.2.1'); el('scanScriptAppend').handlers.click();
assert.equal(values.get(key), before); // No duplicate address in an existing block.
values.set(key, JSON.stringify([{type: 'target', targets: '', protocol: 'ssh', port: '22', password: 'secret'}, {type: 'command', value: 'status'}]));
send('192.0.2.5');
assert.equal(read()[0].protocol, 'ssh');
assert.equal(read()[0].password, '');
assert.equal(read()[1].value, 'status');
values.set(key, JSON.stringify([{type: 'command', value: 'keep'}]));
send('192.0.2.6');
assert.equal(read()[0].type, 'target');
assert.equal(read()[1].value, 'keep');
values.set(key, 'invalid'); send('192.0.2.7');
assert.equal(values.get(key), 'invalid');
assert.ok(el('scanScriptStatus').textContent);
console.log('Scan script draft: first target, append, new block, cancel, duplicate, preservation and invalid draft passed.');
