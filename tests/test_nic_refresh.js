const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
let requests = 0;
let busy = false;
let layout;
const intervals = [];
const timeouts = [];
const control = {checked: true, addEventListener() {}};
const grid = {
    dataset: {statusUrl: '/nics/status', nicLayout: 'cached-layout'},
    inert: true,
    addEventListener() {},
    querySelectorAll: () => [],
    removeAttribute(name) { if (name === 'inert') this.inert = false; },
};
const context = {
    document: {
        hidden: false, body: {dataset: {}},
        querySelector: selector => selector === '.nic-grid' ? grid : busy,
        querySelectorAll: () => [],
        getElementById: () => control,
        addEventListener() {},
    },
    URL,
    window: {location: {href: 'http://localhost/'}},
    localStorage: {getItem: () => null},
    setInterval: (fn, delay) => intervals.push({fn, delay}),
    setTimeout: (fn, delay) => timeouts.push({fn, delay}),
    clearTimeout() {},
    fetch: async url => {
        requests++;
        layout = url.searchParams.get('layout');
        return {ok: true, json: async () => ({nics: [], layout: 'current-layout'})};
    },
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
    assert.equal(layout, 'cached-layout');
    assert.equal(grid.dataset.nicLayout, 'current-layout');
    assert.equal(grid.inert, false);
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
