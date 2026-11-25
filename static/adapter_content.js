(() => {
    const target = document.querySelector('[data-adapter-url], .nic-grid');
    const loading = document.getElementById('adapterLoading');
    const status = loading.querySelector('[role="status"]');
    const retry = loading.querySelector('button');

    async function loadAdapters() {
        retry.hidden = true;
        status.textContent = 'Loading network adapters…';
        target.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(target.dataset.adapterUrl);
            if (!response.ok) throw new Error('Could not load network adapters.');
            target.innerHTML = await response.text();
            const layout = response.headers.get('X-NIC-Layout');
            if (layout) target.dataset.nicLayout = layout;
        } catch (_error) {
            target.removeAttribute('aria-busy');
            status.textContent = 'Could not load network adapters.';
            retry.hidden = false;
            return;
        }
        target.removeAttribute('aria-busy');
        delete target.dataset.adapterUrl;
        loading.hidden = true;
        // Let the draft observer restore select values before notifying each page.
        queueMicrotask(() => target.dispatchEvent(new Event('adapters-loaded')));
    }

    retry.addEventListener('click', loadAdapters);
    if (target.dataset.adapterUrl) loadAdapters();
    else {
        loading.hidden = true;
        target.dispatchEvent(new Event('adapters-loaded'));
    }
})();
