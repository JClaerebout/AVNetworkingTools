// Keep unfinished field values when navigating between tool pages in this tab.
(function preservePageFields() {
    const storageKey = `avNetworkingTools:page-fields:${window.location.pathname}`;
    const fieldSelector = 'input:not([type="hidden"]):not([type="file"]):not([type="button"]):not([type="submit"]), select, textarea';
    let savedFields = {};

    try {
        savedFields = JSON.parse(sessionStorage.getItem(storageKey) || '{}');
    } catch (_error) {
        savedFields = {};
    }

    function fieldKey(field) {
        if (field.id) return `id:${field.id}`;

        const form = field.closest('form');
        const formIdentity = form
            ? form.id || form.querySelector('[name="interface"]')?.value || Array.from(document.forms).indexOf(form)
            : 'page';
        const fieldIdentity = field.name || Array.from(field.classList).join('.') || field.tagName.toLowerCase();
        const matchingFields = Array.from(document.querySelectorAll(fieldSelector))
            .filter(candidate => {
                const candidateForm = candidate.closest('form');
                const candidateFormIdentity = candidateForm
                    ? candidateForm.id || candidateForm.querySelector('[name="interface"]')?.value || Array.from(document.forms).indexOf(candidateForm)
                    : 'page';
                const candidateIdentity = candidate.name || Array.from(candidate.classList).join('.') || candidate.tagName.toLowerCase();
                return candidateFormIdentity === formIdentity && candidateIdentity === fieldIdentity;
            });

        return `field:${formIdentity}:${fieldIdentity}:${matchingFields.indexOf(field)}`;
    }

    function fieldState(field) {
        if (field.type === 'checkbox' || field.type === 'radio') {
            return {checked: field.checked};
        }
        return {value: field.value};
    }

    function restoreField(field) {
        const state = savedFields[fieldKey(field)];
        if (!state) return;

        if (field.type === 'checkbox' || field.type === 'radio') {
            field.checked = !!state.checked;
            return;
        }

        if (field.tagName === 'SELECT' && !Array.from(field.options).some(option => option.value === state.value)) {
            return;
        }
        field.value = state.value;
    }

    function saveAllFields() {
        const state = {};
        document.querySelectorAll(fieldSelector).forEach(field => {
            state[fieldKey(field)] = fieldState(field);
        });

        try {
            sessionStorage.setItem(storageKey, JSON.stringify(state));
            savedFields = state;
        } catch (_error) {
            // The page remains usable when browser storage is unavailable.
        }
    }

    document.querySelectorAll(fieldSelector).forEach(restoreField);

    document.addEventListener('input', event => {
        if (event.target.matches?.(fieldSelector)) saveAllFields();
    });
    document.addEventListener('change', event => {
        if (event.target.matches?.(fieldSelector)) saveAllFields();
    });
    window.addEventListener('pagehide', saveAllFields);

    // Some selects, such as connection history and COM ports, receive options asynchronously.
    new MutationObserver(mutations => {
        mutations.forEach(mutation => {
            if (mutation.target instanceof HTMLSelectElement) restoreField(mutation.target);
        });
    }).observe(document.body, {childList: true, subtree: true});
})();

function updateStaticVisibility(form) {
    const mode = form.querySelector('.mode-select').value;
    const staticFields = form.querySelectorAll('.static-fields');

    staticFields.forEach(el => {
        el.style.display = mode === 'static' ? 'grid' : 'none';
    });
}

function updateModeChangeState(form) {
    const modeSelect = form.querySelector('.mode-select');
    if (!modeSelect) return;

    if (modeSelect.value === form.dataset.appliedMode) {
        delete form.dataset.modeChangePending;
    } else {
        form.dataset.modeChangePending = 'true';
    }
}

document.querySelectorAll('.config-form').forEach(form => {
    const modeSelect = form.querySelector('.mode-select');
    const historySelect = form.querySelector('.history-select');

    if (!modeSelect) return;

    form.dataset.appliedMode = modeSelect.value;
    updateStaticVisibility(form);

    modeSelect.addEventListener('change', () => {
        updateStaticVisibility(form);
        updateModeChangeState(form);
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
            updateModeChangeState(form);
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
        const failed = flash.classList.contains('error');
        showOperationNotice(flash.textContent, failed ? 'error' : 'success');
        return !failed;
    } else {
        showOperationNotice('Network changes completed.', 'success');
        return true;
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
            const operationSucceeded = showResponseNotice(request.responseText);
            if (form.classList.contains('config-form') && operationSucceeded) {
                form.dataset.appliedMode = form.querySelector('.mode-select').value;
                delete form.dataset.modeChangePending;
            }
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

    function hasUnappliedModeChange() {
        return Boolean(document.querySelector('.config-form[data-mode-change-pending="true"]'));
    }

    setInterval(() => {

        const nicPage =
            window.location.pathname === "/" ||
            window.location.pathname === "";

        if (!nicPage)
            return;

        if (hasUnappliedModeChange())
            return;

        if (document.body.dataset.updateInProgress === 'true')
            return;

        if (autoRefresh.checked)
            window.location.reload();

    }, 15000);
}
