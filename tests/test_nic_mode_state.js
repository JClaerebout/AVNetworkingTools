const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');

const key = 'avNetworkingTools:page-fields:/';
const stored = new Map([[key, JSON.stringify({'field:Wifi Link:mode:0': {value: 'dhcp'}})]]);
const events = {};
const mode = {value: 'static', name: 'mode', type: 'select-one', tagName: 'SELECT', options: [{value: 'static'}, {value: 'dhcp'}],
    classList: ['mode-select'], closest: selector => selector === '.config-form' ? {} : null};
const context = {
    window: {location: {pathname: '/'}, addEventListener(name, callback) { events[name] = callback; }},
    document: {body: {}, forms: [], addEventListener() {}, querySelectorAll: () => [mode]},
    sessionStorage: {get length() { return stored.size; }, key: i => [...stored.keys()][i],
        getItem: k => stored.get(k) || null, setItem: (k, v) => stored.set(k, v)},
    MutationObserver: class {observe() {}}, HTMLSelectElement: class {}
};
vm.createContext(context);
const draftStart = source.indexOf('(function preservePageFields()');
const draftEnd = source.indexOf('\nfunction updateStaticVisibility', draftStart);
vm.runInContext(source.slice(draftStart, draftEnd), context);
assert.equal(mode.value, 'static', 'saved DHCP draft must not replace current static mode');
events.pagehide();
assert.equal(JSON.parse(stored.get(key))['field:Wifi Link:mode:0'], undefined);

const fields = Object.fromEntries(['ip', 'subnet', 'gateway', 'dns'].map(name => [name, {value: ''}]));
const label = {hidden: true};
const form = {
    dataset: {appliedMode: 'dhcp'},
    querySelector(selector) {
        if (selector === '.mode-select') return mode;
        if (selector === '.mode-pending') return label;
        const match = selector.match(/^\[name="(.+)"\]$/);
        return match ? fields[match[1]] : null;
    },
    querySelectorAll: () => [],
};
const card = {querySelector: selector => selector === '.config-form' ? form : null};
context.document.querySelectorAll = () => [];
const syncStart = source.indexOf('function updateStaticVisibility');
const syncEnd = source.indexOf('\nfunction showOperationNotice', syncStart);
vm.runInContext(source.slice(syncStart, syncEnd), context);
const current = {dhcp_raw: 'Disabled', ip: '192.168.100.121', subnet: '255.255.255.0',
    gateway: '192.168.100.1', dns: ['192.168.0.1']};
context.syncNicForm(card, current);
assert.equal(mode.value, 'static');
assert.equal(fields.ip.value, current.ip);
assert.equal(fields.dns.value, current.dns[0]);
assert.equal(label.hidden, true);

mode.value = 'dhcp';
form.dataset.modeChangePending = 'true';
context.syncNicForm(card, current);
assert.equal(mode.value, 'dhcp', 'deliberate unsaved mode choice should remain');
assert.equal(label.hidden, false, 'unsaved choice must be labelled');
form.dataset.syncAfterRestore = 'true';
context.syncNicForm(card, current);
assert.equal(mode.value, 'static', 'restore must return the form to observed adapter mode');
assert.equal(label.hidden, true);
console.log('NIC mode state tests passed.');
