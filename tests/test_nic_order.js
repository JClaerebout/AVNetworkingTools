const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup(saved) {
    let stored;
    const cards = ['Ethernet', 'Wi-Fi', 'USB'].map(name => ({
        dataset: {orderKey: name}, events: {},
        classList: {contains: () => false, add() {}, remove() {}},
        addEventListener(name, fn) { this.events[name] = fn; },
        querySelector() { return this; }, focus() {},
        get nextSibling() { return grid.items[grid.items.indexOf(this) + 1] || null; }
    }));
    const grid = {
        addEventListener(event, listener) { if (event === 'adapters-loaded') listener(); },
        items: [...cards], querySelectorAll() { return this.items; },
        append(card) { this.insertBefore(card, null); },
        insertBefore(card, target) {
            this.items.splice(this.items.indexOf(card), 1);
            this.items.splice(target ? this.items.indexOf(target) : this.items.length, 0, card);
        }
    };
    vm.runInNewContext(fs.readFileSync('static/nic_order.js', 'utf8'), {
        document: {querySelector: () => grid},
        window: {workspaceStorage: {getItem: () => saved, setItem: (_, value) => { stored = JSON.parse(value); }}}
    });
    return {cards, grid, stored: () => stored};
}
const names = grid => grid.items.map(card => card.dataset.orderKey);
const state = setup('["Wi-Fi","Disconnected","Ethernet"]');
assert.deepEqual(names(state.grid), ['Wi-Fi', 'Ethernet', 'USB']);
const event = {preventDefault() {}, dataTransfer: {setData() {}, setDragImage() {}}};
state.cards[2].events.dragstart(event);
state.cards[1].events.drop(event);
state.cards[2].events.dragend();
assert.deepEqual(names(state.grid), ['USB', 'Wi-Fi', 'Ethernet']);
assert.deepEqual(state.stored(), ['USB', 'Disconnected', 'Wi-Fi', 'Ethernet']);
state.cards[2].events.keydown({key: 'ArrowRight', preventDefault() {}});
assert.deepEqual(names(state.grid), ['Wi-Fi', 'USB', 'Ethernet']);
const restored = setup(JSON.stringify(state.stored()));
assert.deepEqual(names(restored.grid), names(state.grid));
assert.deepEqual(names(setup('{bad json').grid), ['Ethernet', 'Wi-Fi', 'USB']);
assert.deepEqual(names(setup('{}').grid), ['Ethernet', 'Wi-Fi', 'USB']);
console.log('NIC ordering tests passed: drag, keyboard, persistence, disconnected and new adapters.');
