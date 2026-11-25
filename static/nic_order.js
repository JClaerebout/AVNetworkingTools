// Card order is a workspace preference shared by the desktop platforms.
const orderedNicGrid = document.querySelector('.nic-grid');
orderedNicGrid.addEventListener('adapters-loaded', () => {
    const grid = orderedNicGrid;
    const key = 'avNetworkingTools:nic-order:v1';
    const storage = window.workspaceStorage || localStorage;
    const cards = () => Array.from(grid.querySelectorAll('.nic-card'));
    let saved = [];
    try {
        const value = JSON.parse(storage.getItem(key) || '[]');
        if (Array.isArray(value) && value.every(item => typeof item === 'string')) saved = value;
    } catch (_) {}
    const initial = cards();
    // Unknown adapters follow saved adapters, preserving their default order.
    [...initial].sort((a, b) => {
        const rank = card => saved.includes(card.dataset.orderKey) ? saved.indexOf(card.dataset.orderKey) : saved.length;
        return rank(a) - rank(b);
    }).forEach(card => grid.append(card));
    let dragged = null;
    function clearTargets() {
        cards().forEach(card => card.classList.remove('is-drop-target'));
    }
    function save() {
        const visible = cards().map(card => card.dataset.orderKey);
        // Retain disconnected adapters so they can regain their position later.
        let index = 0;
        saved = saved.map(item => visible.includes(item) ? visible[index++] : item);
        saved.push(...visible.slice(index));
        try { storage.setItem(key, JSON.stringify(saved)); } catch (_) {}
    }
    cards().forEach(card => {
        const handle = card.querySelector('.nic-drag-handle');
        handle.addEventListener('dragstart', event => {
            if (card.classList.contains('is-busy')) { event.preventDefault(); return; }
            dragged = card;
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', card.dataset.orderKey);
            event.dataTransfer.setDragImage(card, 20, 20);
            card.classList.add('is-dragging');
        });
        handle.addEventListener('dragend', () => {
            dragged = null;
            card.classList.remove('is-dragging');
            clearTargets();
        });
        card.addEventListener('dragover', event => {
            if (!dragged || dragged === card) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            clearTargets();
            card.classList.add('is-drop-target');
        });
        card.addEventListener('dragleave', event => {
            if (!card.contains(event.relatedTarget)) card.classList.remove('is-drop-target');
        });
        card.addEventListener('drop', event => {
            if (!dragged || dragged === card) return;
            event.preventDefault();
            const order = cards();
            grid.insertBefore(dragged, order.indexOf(dragged) < order.indexOf(card) ? card.nextSibling : card);
            clearTargets();
            save();
        });
        handle.addEventListener('keydown', event => {
            const delta = {ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1}[event.key];
            if (!delta || card.classList.contains('is-busy')) return;
            event.preventDefault();
            const order = cards();
            const target = order[order.indexOf(card) + delta];
            if (!target) return;
            grid.insertBefore(card, delta > 0 ? target.nextSibling : target);
            handle.focus();
            save();
        });
    });
});
