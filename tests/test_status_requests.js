const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const pending = [];
const timers = new Map();
let nextTimer = 0;
const window = {};
const context = {
    window, Date, AbortController,
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url) { return new Promise(resolve => pending.push({url, resolve})); }
};
vm.runInNewContext(fs.readFileSync('static/status_requests.js', 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const reply = (request, data, status = 200) => request.resolve({ok: status < 400, status, json: async () => data});

(async () => {
    const seen = [];
    const indicator = {textContent: ''};
    const poller = window.AVRequests.createStatusPoller({
        url: '/status', interval: 1000, onData: data => seen.push(data.value), indicator
    });
    poller.start();
    poller.poll();
    assert.equal(pending.length, 1, 'only one status request may be in flight');

    const action = poller.action('/start', {method: 'POST'});
    assert.equal(pending.length, 2);
    reply(pending[1], {success: false, message: 'Select a NIC', value: 'action', running: false}, 409);
    await action;
    assert.match(indicator.textContent, /Action failed: Select a NIC/);
    assert.match(indicator.textContent, /Last updated/);

    reply(pending[0], {value: 'old'});
    await flush();
    assert.deepEqual(seen, ['action'], 'an earlier status response must not replace the action');

    const timer = [...timers.values()].at(-1);
    timer();
    reply(pending[2], {value: 'fresh'});
    await flush();
    assert.deepEqual(seen, ['action', 'fresh']);
    assert.match(indicator.textContent, /Action failed: Select a NIC/, 'polling must retain an action error');
    const badAction = poller.action('/start', {method: 'POST'});
    reply(pending[3], {success: false, message: 'Expected a JSON object.'}, 400);
    await badAction;
    assert.deepEqual(seen, ['action', 'fresh'], 'validation errors must not reset displayed status');
    assert.match(indicator.textContent, /Expected a JSON object/);
    poller.stop();
    console.log('Status request tests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
