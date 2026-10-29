const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element(value = '') {
    return {value, textContent: '', disabled: false, events: {}, children: [],
        addEventListener(name, fn) { this.events[name] = fn; },
        replaceChildren() { this.children = []; this.value = ''; },
        append(option) { this.children.push(option); },
        showModal() { this.open = true; }, close() { this.open = false; }};
}
const elements = Object.fromEntries(['workspaceDialog', 'workspaceList', 'workspaceName', 'workspaceMessage', 'workspaceOpen', 'workspaceClose'].map(id => [id, element()]));
elements.workspaceDrafts = {textContent: '{}'};
const buttons = ['save', 'reload', 'delete', 'new', 'rename'].map(action => Object.assign(element(), {dataset: {workspaceAction: action}}));
const events = {}, calls = [], confirmations = [];
let names = ['Hall A'], reloads = 0, promptResult = null;
const prompts = [];
const context = {
    document: {getElementById: id => elements[id], querySelectorAll: () => buttons,
        addEventListener: (name, fn) => {events[name] = fn;}, createElement: () => element()},
    window: {addEventListener() {}, prompt(message, value) { prompts.push({message, value}); return promptResult; }, confirm(message) {confirmations.push(message); return true;}, location: {reload() {reloads++;}}},
    setTimeout, clearTimeout, queueMicrotask,
    fetch: async (url, options) => {
        if (!options) return {ok: true, json: async () => ({workspaces: names})};
        const body = JSON.parse(options.body);
        calls.push({url, body});
        if (url.endsWith('/save') && !names.includes(body.name)) names.push(body.name);
        if (url.endsWith('/delete')) names = names.filter(name => name !== body.name);
        return {ok: true, json: async () => ({success: true})};
    }
};
vm.runInNewContext(fs.readFileSync('static/workspace_state.js', 'utf8'), context);
const click = action => buttons.find(button => button.dataset.workspaceAction === action).events.click();
(async () => {
    events.DOMContentLoaded();
    await elements.workspaceOpen.events.click();
    assert.equal(elements.workspaceDialog.open, true);
    assert.equal(elements.workspaceName.value, 'new workspace');
    assert.equal(elements.workspaceList.children[0].textContent, 'Hall A');
    elements.workspaceName.value = 'Hall B';
    await click('save');
    assert.deepEqual(calls.at(-1), {url: '/api/workspace/save', body: {name: 'Hall B', overwrite: false, new_name: null}});
    await click('save');
    assert.equal(calls.at(-1).body.overwrite, true);
    assert.match(confirmations.at(-1), /Replace saved workspace/);
    elements.workspaceList.value = 'Hall A';
    elements.workspaceList.events.change();
    assert.equal(elements.workspaceName.value, 'Hall B');
    const count = calls.length;
    await click('rename');
    assert.equal(calls.length, count); // Cancel must leave the saved workspace untouched.
    promptResult = 'Renamed hall';
    await click('rename');
    assert.equal(calls.at(-1).body.new_name, 'Renamed hall');
    assert.equal(prompts.at(-1).value, 'Hall A');
    assert.equal(elements.workspaceName.value, 'Hall B');
    elements.workspaceList.value = 'Hall A';
    await click('delete');
    assert.equal(calls.at(-1).url, '/api/workspace/delete');
    assert.equal(reloads, 0);
    assert.equal(elements.workspaceList.children.length, 1);
    elements.workspaceList.value = 'Hall B';
    await click('reload');
    assert.equal(calls.at(-1).body.name, 'Hall B');
    assert.equal(reloads, 1);
    console.log('Named workspace menu tests passed: list, save, overwrite, select, delete and reload.');
})().catch(error => { console.error(error); process.exitCode = 1; });
