const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function control() {
    return {
        value: '', textContent: '', listeners: {},
        addEventListener(name, callback) { this.listeners[name] = callback; },
        fire(name) { this.listeners[name](); }
    };
}

const output = {
    textContent: '', scrollTop: 0, clientHeight: 20,
    get scrollHeight() { return this.textContent.split('\n').length * 10; }
};
const search = control();
const count = control();
const latest = control();
const window = {};
vm.runInNewContext(fs.readFileSync('static/log_view.js', 'utf8'), {window});
const view = window.AVLogView.createLogView({output, search, count, latest});

view.render(['one', 'two', 'three', 'four', 'five', 'six'], 'Empty');
assert.equal(output.scrollTop, output.scrollHeight);

output.scrollTop = 0;
view.render(['one', 'two', 'three', 'four', 'five', 'six', 'seven'], 'Empty');
assert.equal(output.scrollTop, 0, 'new output must not move a reader who scrolled up');

search.value = 'TWO';
search.fire('input');
assert.equal(output.textContent, 'two');
assert.equal(count.textContent, '1 match');

search.value = 'missing';
search.fire('input');
assert.equal(output.textContent, 'No matching log entries.');
assert.equal(count.textContent, '0 matches');

latest.fire('click');
assert.equal(search.value, '');
assert.equal(output.scrollTop, output.scrollHeight);
assert.match(output.textContent, /seven/);

view.clearView();
assert.equal(output.textContent, 'Log view cleared.');
view.render(['one', 'two', 'three', 'four', 'five', 'six', 'seven'], 'Empty');
assert.equal(output.textContent, 'Log view cleared.', 'same status must not undo Clear view');
view.render(['new'], 'Empty');
assert.equal(output.textContent, 'new');

const pingOutput = {
    textContent: '', scrollTop: 0, clientHeight: 20,
    get scrollHeight() { return this.textContent.split('\n').length * 10; }
};
const pingLatest = control();
const pingView = window.AVLogView.createLogView({output: pingOutput, latest: pingLatest});
pingView.render(['reply one', 'reply two'], 'No output yet.');
pingLatest.fire('click');
assert.equal(pingOutput.scrollTop, pingOutput.scrollHeight);

const connectionOutput = {
    textContent: '', scrollTop: 0, clientHeight: 20,
    get scrollHeight() { return this.textContent.split('\n').length * 10; }
};
const connectionSearch = control();
const connectionCount = control();
const connectionLatest = control();
let autoScroll = false;
const connectionView = window.AVLogView.createLogView({
    output: connectionOutput, search: connectionSearch, count: connectionCount,
    latest: connectionLatest, separator: '\n\n', follow: () => autoScroll
});
connectionView.render(['[10:00] TX\nPOWER', '[10:01] RX\nOK'], 'No data yet.');
assert.equal(connectionOutput.scrollTop, 0, 'disabled auto scroll must remain disabled');
connectionSearch.value = 'OK';
connectionSearch.fire('input');
assert.equal(connectionOutput.textContent, '[10:01] RX\nOK', 'search preserves the complete exchange');
assert.equal(connectionCount.textContent, '1 match');
connectionLatest.fire('click');
assert.equal(connectionOutput.scrollTop, connectionOutput.scrollHeight);

console.log('Log view tests passed.');
