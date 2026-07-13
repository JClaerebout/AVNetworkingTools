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

    // Do not let auto refresh discard values that the user is entering.
    form.addEventListener('input', () => {
        form.dataset.userEdited = 'true';
    });
    form.addEventListener('change', () => {
        form.dataset.userEdited = 'true';
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

function showOperationNotice(message, category = 'info') {
    const notice = document.getElementById('operationNotice');
    if (!notice) return;

    notice.className = `flash ${category}`;
    notice.textContent = message;
    notice.hidden = false;
    notice.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function operationMessage(form) {
    if (form.id.startsWith('release-')) return 'Releasing DHCP lease...';
    if (form.id.startsWith('renew-')) return 'Renewing DHCP lease...';

    return form.querySelector('[name="mode"]')?.value === 'dhcp'
        ? 'Applying DHCP settings...'
        : 'Applying static IP settings...';
}

function showNicBusyOverlay(form, message) {
    const card = form.closest('.nic-card');
    if (!card) return null;

    const overlay = card.querySelector('.nic-card-overlay');
    const progressText = card.querySelector('.nic-card-progress span:last-child');
    card.classList.add('is-busy');
    card.setAttribute('aria-busy', 'true');

    if (progressText) progressText.textContent = message;
    if (overlay) overlay.setAttribute('aria-hidden', 'false');

    return card;
}

function hideNicBusyOverlay(card) {
    if (!card) return;

    card.classList.remove('is-busy');
    card.removeAttribute('aria-busy');
    card.querySelector('.nic-card-overlay')?.setAttribute('aria-hidden', 'true');
}

function showResponseNotice(responseHtml) {
    const response = document.createElement('template');
    response.innerHTML = responseHtml;
    const flash = response.content.querySelector('.flash');

    if (flash) {
        showOperationNotice(flash.textContent, flash.classList.contains('error') ? 'error' : 'success');
    } else {
        showOperationNotice('Network changes completed.', 'success');
    }
}

document.querySelectorAll('.config-form, form[id^="release-"], form[id^="renew-"]').forEach(form => {
    form.addEventListener('submit', event => {
        event.preventDefault();
        const message = operationMessage(form);
        const busyCard = showNicBusyOverlay(form, message);
        showOperationNotice(message);

        const request = new XMLHttpRequest();
        request.open('POST', form.action, true);
        request.onload = () => {
            if (request.status < 200 || request.status >= 400) {
                hideNicBusyOverlay(busyCard);
                showOperationNotice('Network change failed. Please try again.', 'error');
                return;
            }
            if (form.classList.contains('config-form')) {
                delete form.dataset.userEdited;
            }
            showResponseNotice(request.responseText);
            hideNicBusyOverlay(busyCard);
        };
        request.onerror = () => {
            hideNicBusyOverlay(busyCard);
            showOperationNotice('Network change failed. Please try again.', 'error');
        };
        request.send(new FormData(form));
    });
});

const autoRefresh = document.getElementById('autoRefresh');

if (autoRefresh) {
    const saved = localStorage.getItem('autoRefreshEnabled');

    if (saved === null) {
        autoRefresh.checked = true;
        localStorage.setItem('autoRefreshEnabled', 'true');
    } else {
        autoRefresh.checked = saved === 'true';
    }

    autoRefresh.addEventListener('change', () => {
        localStorage.setItem('autoRefreshEnabled', autoRefresh.checked ? 'true' : 'false');
    });

    function pageIsActive() {
        return document.visibilityState === 'visible' && document.hasFocus();
    }

    function userIsEditingNicSettings() {
        const activeElement = document.activeElement;
        const isUsingConfigForm = activeElement && activeElement.closest('.config-form');
        const hasUnsavedValues = document.querySelector('.config-form[data-user-edited="true"]');

        return Boolean(isUsingConfigForm || hasUnsavedValues);
    }

    setInterval(() => {

        const nicPage =
            window.location.pathname === "/" ||
            window.location.pathname === "";

        if (!nicPage)
            return;

        if (!pageIsActive())
            return;

        if (userIsEditingNicSettings())
            return;

        if (autoRefresh.checked)
            window.location.reload();

    }, 15000);
}
