const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/workspace_state.js', 'utf8');
const calls = [], events = {};
let timer;
const context = {
    window: {addEventListener: (name, fn) => { events[name] = fn; }},
    document: {getElementById: () => ({textContent: '{"existing":"restored"}'}), addEventListener() {}},
    setTimeout: fn => { timer = fn; }, clearTimeout() {}, queueMicrotask,
    fetch: async (url, options) => { calls.push(JSON.parse(options.body)); return {ok: true}; }
};
vm.runInNewContext(source, context);
const storage = context.window.workspaceStorage;
assert.equal(storage.getItem('existing'), 'restored');
assert.equal(storage.getItem('missing'), null);
storage.setItem('field', 'first');
storage.setItem('field', 'latest');
(async () => {
    await timer();
    assert.deepEqual(calls, [{key: 'field', value: 'latest'}]);
    storage.setItem('field', 'on close');
    events.pagehide();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.at(-1).value, 'on close');
    // A failed save remains pending for retry, without looping in the same flush.
    context.fetch = async () => { throw new Error('offline'); };
    storage.setItem('field', 'retry');
    await timer();
    context.fetch = async (url, options) => { calls.push(JSON.parse(options.body)); return {ok: true}; };
    await timer();
    assert.equal(calls.at(-1).value, 'retry');
    console.log('Workspace draft tests passed: restore, debounce, close and retry.');
})().catch(error => { console.error(error); process.exitCode = 1; });
