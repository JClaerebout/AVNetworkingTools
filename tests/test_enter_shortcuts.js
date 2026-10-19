const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let handler;
let buttons = {};
let pending = [];
let focusedForm = null;
let row = null;
let field = '';
let excluded = false;
let clicks = [];
const target = {
    closest(selector) {
        if (selector === '.config-form' || selector === 'form') return focusedForm;
        if (selector === '.send-row') return row;
        return excluded ? {} : null;
    },
    matches(selector) { return selector.split(', ').includes(field); }
};
vm.runInNewContext(fs.readFileSync('static/enter_shortcuts.js', 'utf8'), {
    document: {
        addEventListener(name, callback) { handler = callback; },
        getElementById(id) { return buttons[id]; },
        querySelectorAll() { return pending; }
    }
});
function button(id, disabled = false) {
    return {disabled, click() { clicks.push(id); }};
}
function key(key = 'Enter', extra = {}) {
    let prevented = false;
    handler({key, target, preventDefault() { prevented = true; }, ...extra});
    return prevented;
}
for (const id of ['startScan', 'startPing', 'startMulticast', 'startWifiScan']) {
    buttons = {[id]: button(id)};
    clicks = [];
    assert.equal(key(), true);
    assert.deepEqual(clicks, [id]);
    buttons[id].disabled = true;
    key(); assert.equal(clicks.length, 1);
}
buttons = {connectButton: button('connect')};
for (field of ['#connHost', '#connPort']) {
    clicks = []; key(); assert.deepEqual(clicks, ['connect']);
}
row = {querySelector() { return button('row-two'); }};
for (field of ['.send-input', '.send-name-input']) {
    clicks = []; key(); assert.deepEqual(clicks, ['row-two']);
}
field = ''; row = null;
for (const id of ['disconnectButton', 'stopScan', 'stopPing', 'stopMulticast', 'stopWifiScan', 'stopScript']) {
    buttons = {[id]: button(id)}; clicks = [];
    key('Escape'); assert.deepEqual(clicks, [id]);
    buttons[id].disabled = true;
    key('Escape'); assert.equal(clicks.length, 1);
}
buttons = {startWifiScan: button('start')}; clicks = [];
for (const extra of [{repeat:true}, {isComposing:true}, {ctrlKey:true}, {altKey:true}, {metaKey:true}, {shiftKey:true}, {defaultPrevented:true}]) key('Enter', extra);
excluded = true; key(); excluded = false;
assert.deepEqual(clicks, []);
buttons = {};
let submitted = 0;
let busy = false;
const apply = button('apply');
const form = {
    closest() { return {classList: {contains() { return busy; }}}; },
    querySelector() { return apply; },
    requestSubmit(submitter) { assert.equal(submitter, apply); submitted++; }
};
focusedForm = form; key(); assert.equal(submitted, 1);
busy = true; key(); assert.equal(submitted, 1); busy = false;
focusedForm = null; pending = [form]; key(); assert.equal(submitted, 2);
pending = [form, form]; key(); assert.equal(submitted, 2);
console.log('Enter and Escape shortcut tests passed.');
