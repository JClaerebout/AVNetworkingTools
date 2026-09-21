const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
let requests = 0;
let busy = false;
const intervals = [];
const timeouts = [];
const control = {checked: true, addEventListener() {}};
const context = {
    document: {
        hidden: false, body: {dataset: {}},
        querySelector: () => busy,
        querySelectorAll: () => [],
        getElementById: () => control,
        addEventListener() {},
    },
    localStorage: {getItem: () => null},
    setInterval: (fn, delay) => intervals.push({fn, delay}),
    setTimeout: (fn, delay) => timeouts.push({fn, delay}),
    clearTimeout() {},
    fetch: async () => { requests++; return {ok: true, json: async () => ({nics: []})}; },
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('let nicRefreshInFlight')), context);
(async () => {
    assert.equal(intervals[0].delay, 10000);
    context.document.hidden = true;
    await context.refreshNicStatus();
    assert.equal(requests, 0);
    context.document.hidden = false;
    busy = true;
    await context.refreshNicStatus();
    assert.equal(requests, 0);
    busy = false;
    await context.refreshNicStatus();
    assert.equal(requests, 1);
    context.startNicRefreshBurst();
    await new Promise(resolve => setImmediate(resolve));
    for (let i = 0; i < 5; i++) {
        const timeout = timeouts.shift();
        assert.equal(timeout.delay, 2000);
        await timeout.fn();
    }
    assert.equal(requests, 7);
    assert.equal(timeouts.length, 0);
    console.log('NIC refresh tests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
