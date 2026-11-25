document.addEventListener('DOMContentLoaded', () => {
    const dialog = document.getElementById('workspaceImportDialog');
    const list = document.getElementById('workspaceImportItem');
    const name = document.getElementById('workspaceImportName');
    const status = document.getElementById('workspaceImportStatus');
    const submit = document.getElementById('workspaceImportConfirm');
    let items = [], kind, busy = false;
    list.addEventListener('change', () => { name.value = items[list.value]?.name || ''; });
    document.getElementById('workspaceImportClose').addEventListener('click', () => dialog.close());
    document.querySelectorAll('[data-workspace-import]').forEach(button => {
        button.addEventListener('click', async () => {
            kind = button.dataset.workspaceImport;
            name.maxLength = Number(button.dataset.importNameLimit || 80);
            list.replaceChildren(); name.value = ''; submit.disabled = true;
            status.textContent = 'Loading available saves…'; dialog.showModal();
            try {
                const response = await fetch(`/api/workspace-import/${kind}`);
                if (!response.ok) throw new Error('Could not list saved items.');
                items = (await response.json()).items;
                items.forEach((item, index) => {
                    const option = document.createElement('option');
                    option.value = index; option.textContent = `${item.workspace} — ${item.name}`;
                    list.append(option);
                });
                name.value = items[0]?.name || '';
                submit.disabled = !items.length;
                status.textContent = items.length ? '' : 'No saves available in other saved workspaces.';
            } catch (error) { status.textContent = error.message; }
        });
    });
    submit.addEventListener('click', async () => {
        const item = items[list.value];
        if (!item || busy) return;
        busy = true; submit.disabled = true;
        try {
            const response = await fetch(`/api/workspace-import/${kind}`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...item, new_name: name.value.trim()})
            });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message === 'NAME_EXISTS'
                ? 'This name already exists. Choose another import name.' : result.message || 'Import failed.');
            document.dispatchEvent(new CustomEvent('workspace-library-imported', {detail: {kind, name: name.value.trim()}}));
            status.textContent = 'Imported. If this workspace has no saved name yet, save it to keep the item available in other workspaces.';
        } catch (error) { status.textContent = error.message; }
        finally { busy = false; submit.disabled = false; }
    });
});
