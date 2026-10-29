// The server owns drafts because the desktop listener's port changes on restart.
(() => {
    const initial = document.getElementById('workspaceDrafts');
    const drafts = JSON.parse(initial.textContent);
    const pending = new Map();
    let timer;
    let saving = null;
    function flush() {
        if (saving) return saving;
        saving = flushPending().finally(() => { saving = null; });
        return saving;
    }
    async function flushPending() {
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
    let switching = false;
    document.addEventListener('DOMContentLoaded', () => {
        const dialog = document.getElementById('workspaceDialog');
        const list = document.getElementById('workspaceList');
        const nameInput = document.getElementById('workspaceName');
        const message = document.getElementById('workspaceMessage');
        const buttons = document.querySelectorAll('[data-workspace-action]');
        let names = [];
        function updateButtons() {
            buttons.forEach(button => {
                button.disabled = switching || (['reload', 'rename', 'delete'].includes(button.dataset.workspaceAction) && !list.value);
            });
        }
        async function refresh(selected) {
            const response = await fetch('/api/workspaces');
            if (!response.ok) throw new Error('Could not list saved workspaces.');
            const data = await response.json();
            names = data.workspaces;
            if (selected === undefined) nameInput.value = data.current_name || 'new workspace';
            list.replaceChildren();
            names.forEach(name => {
                const option = document.createElement('option');
                option.value = name;
                option.textContent = name;
                list.append(option);
            });
            list.value = names.includes(selected) ? selected : '';
            updateButtons();
        }
        list.addEventListener('change', () => {
            updateButtons();
        });
        document.getElementById('workspaceOpen').addEventListener('click', async () => {
            dialog.showModal();
            message.textContent = 'Loading workspaces…';
            switching = true;
            updateButtons();
            try { await refresh(); message.textContent = ''; }
            catch (error) { message.textContent = error.message; }
            finally { switching = false; updateButtons(); }
        });
        document.getElementById('workspaceClose').addEventListener('click', () => dialog.close());
        buttons.forEach(button => {
            button.addEventListener('click', async () => {
                if (switching) return;
                const action = button.dataset.workspaceAction;
                const name = action === 'save' ? nameInput.value.trim() : list.value;
                if (action !== 'new' && !name) {
                    message.textContent = action === 'save' ? 'Enter a workspace name.' : 'Select a saved workspace.';
                    return;
                }
                const enteredName = action === 'rename' ? window.prompt('Rename workspace:', name) : null;
                if (action === 'rename' && enteredName === null) return;
                const newName = enteredName === null ? null : enteredName.trim();
                if (action === 'rename' && !newName) {
                    message.textContent = 'Enter a name for the selected workspace.';
                    return;
                }
                const overwrite = action === 'save' && names.includes(name);
                const messages = {
                    save: `Replace saved workspace “${name}” with the current fields, results, logs, histories, setups and scripts?`,
                    reload: `Reload “${name}” and replace current fields, results, logs, histories, setups and scripts? Tasks stay stopped and network settings are not applied.`,
                    rename: `Rename “${name}” to “${newName}”?`,
                    delete: `Delete saved workspace “${name}”? Your current workspace will remain open.`,
                    new: 'Clear current fields, results, logs, histories, setups and scripts? Saved workspaces will remain available; the new workspace starts with empty histories, setups and scripts.'
                };
                if (action !== 'rename' && (action !== 'save' || overwrite) && !window.confirm(messages[action])) return;
                switching = true;
                updateButtons();
                message.textContent = '';
                try {
                    await flush();
                    if (pending.size) throw new Error('Could not save current fields. Please try again.');
                    const response = await fetch(`/api/workspace/${action}`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({name, overwrite, new_name: newName})
                    });
                    const result = await response.json();
                    if (!response.ok || !result.success) throw new Error(result.message || 'Workspace action failed.');
                    if (['save', 'delete', 'rename'].includes(action)) {
                        if (action === 'save') nameInput.value = name;
                        if (action === 'rename' && nameInput.value === name) nameInput.value = newName;
                        await refresh(action === 'save' ? name : action === 'rename' ? newName : '');
                        message.textContent = action === 'save' ? `Saved “${name}”.` : action === 'rename' ? `Renamed to “${newName}”.` : `Deleted “${name}”.`;
                    } else window.location.reload();
                } catch (error) {
                    message.textContent = error.message;
                } finally {
                    switching = false;
                    updateButtons();
                }
            });
        });
    });
    window.addEventListener('pagehide' , () => { queueMicrotask(flush); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });
})();
