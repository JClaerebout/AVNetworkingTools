function updateStaticVisibility(form) {
    const mode = form.querySelector('.mode-select').value;
    const staticFields = form.querySelectorAll('.static-fields');

    staticFields.forEach(el => {
        el.style.display = mode === 'static' ? 'grid' : 'none';
    });
}

document.querySelectorAll('.config-form').forEach(form => {
    const modeSelect = form.querySelector('.mode-select');
    const historySelect = form.querySelector('.history-select');

    if (!modeSelect) return;

    updateStaticVisibility(form);

    modeSelect.addEventListener('change', () => {
        updateStaticVisibility(form);
    });

    if (historySelect) {
        historySelect.addEventListener('change', event => {
            if (!event.target.value) return;

            const h = JSON.parse(event.target.value);
            form.querySelector('[name="mode"]').value = 'static';
            form.querySelector('[name="ip"]').value = h.ip || '';
            form.querySelector('[name="subnet"]').value = h.subnet || '';
            form.querySelector('[name="gateway"]').value = h.gateway || '';
            form.querySelector('[name="dns"]').value = [h.dns1, h.dns2].filter(Boolean).join(', ');
            updateStaticVisibility(form);
        });
    }
});

const autoRefresh = document.getElementById('autoRefresh');

if (autoRefresh) {
    const saved = localStorage.getItem('autoRefreshEnabled');

    if (saved === null) {
        autoRefresh.checked = false;
        localStorage.setItem('autoRefreshEnabled', 'false');
    } else {
        autoRefresh.checked = saved === 'true';
    }

    autoRefresh.addEventListener('change', () => {
        localStorage.setItem('autoRefreshEnabled', autoRefresh.checked ? 'true' : 'false');
    });

    setInterval(() => {

        const nicPage =
            window.location.pathname === "/" ||
            window.location.pathname === "";

        if (!nicPage)
            return;

        if (document.hidden)
            return;

        if (autoRefresh && autoRefresh.checked)
            window.location.reload();

    }, 15000);
}
