const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const requests = [];
const timers = new Map();
const listeners = {};
let nextTimer = 0;
const classes = new Set();
const container = {hidden: true, classList: {toggle(name, enabled) {
    if (enabled) classes.add(name); else classes.delete(name);
}}};
const label = {textContent: ''};
const links = {children: [], replaceChildren() { this.children = []; }, appendChild(node) { this.children.push(node); }};
const document = {
    currentScript: {dataset: {activityUrl: '/api/activity'}}, hidden: false,
    getElementById(id) { return {activeTasks: container, activeTasksLabel: label, activeTaskLinks: links}[id]; },
    createElement() { return {href: '', textContent: '', className: ''}; },
    addEventListener(name, callback) { listeners[name] = callback; }
};
const window = {
    AVRequests: {jsonRequest: () => new Promise((resolve, reject) => requests.push({resolve, reject}))},
    addEventListener(name, callback) { listeners[name] = callback; }
};
const context = {document, window,
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); }};
vm.runInNewContext(fs.readFileSync('static/activity.js', 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    assert.equal(requests.length, 1);
    listeners.visibilitychange();
    assert.equal(requests.length, 1, 'another poll must not start while one is in flight');
    requests[0].resolve({response: {ok: true}, data: {tasks: [
        {key: 'scan', label: 'IP Scan · Scanning', url: '/ip-scan'},
        {key: 'health', label: 'Health Check', url: '/multicast'}
    ]}});
    await flush();
    assert.equal(container.hidden, false);
    assert.equal(links.children.length, 2, 'both concurrent activities must be shown');
    assert.equal(links.children[0].href, '/ip-scan');
    assert.equal(links.children[1].href, '/multicast');
    assert.equal(label.textContent, 'Active (2):');

    [...timers.values()].at(-1)();
    requests[1].reject(new Error('offline'));
    await flush();
    assert.equal(classes.has('is-stale'), true);
    assert.match(label.textContent, /unavailable/i);
    assert.equal(links.children[0].href, '/ip-scan', 'last known task remains navigable');

    [...timers.values()].at(-1)();
    requests[2].resolve({response: {ok: true}, data: {tasks: []}});
    await flush();
    assert.equal(container.hidden, true);
    listeners.pagehide();
    console.log('Activity indicator tests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
