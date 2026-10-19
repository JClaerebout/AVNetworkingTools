// The server owns drafts because the desktop listener's port changes on restart.
(() => {
    const initial = document.getElementById('workspaceDrafts');
    const drafts = JSON.parse(initial.textContent);
    const pending = new Map();
    let timer;
    let saving = false;
    async function flush() {
        if (saving) return;
        saving = true;
        clearTimeout(timer);
        for (const [key, value] of Array.from(pending)) {
            if (pending.get(key) === value) pending.delete(key);
            try {
                const response = await fetch('/api/workspace/draft', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({key, value}), keepalive: true
                });
                if (!response.ok) throw new Error('Draft save failed');
            } catch (_) {
                if (!pending.has(key)) pending.set(key, value);
            }
        }
        saving = false;
        if (pending.size) timer = setTimeout(flush, 1000);
    }
    window.workspaceStorage = {
        getItem(key) { return Object.hasOwn(drafts, key) ? drafts[key] : null; },
        setItem(key, value) {
            if (drafts[key] === value) return;
            drafts[key] = value;
            pending.set(key, value);
            clearTimeout(timer);
            timer = setTimeout(flush, 200);
        },
        get length() { return Object.keys(drafts).length; },
        key(index) { return Object.keys(drafts)[index]; }
    };
    window.addEventListener('pagehide', () => { queueMicrotask(flush); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });
})();
