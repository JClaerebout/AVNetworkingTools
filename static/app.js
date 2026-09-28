// Cycle through persistent dark, light, and system-controlled themes.
(function enableThemeToggle() {
    const toggle = document.getElementById('themeToggle');
    if (!toggle) return;

    const label = toggle.querySelector('.theme-toggle-label');
    const icon = toggle.querySelector('.theme-toggle-icon');
    const systemPreference = window.matchMedia?.('(prefers-color-scheme: dark)');
    const preferences = ['dark', 'light', 'auto'];

    function resolvedSystemTheme() {
        return systemPreference?.matches ? 'dark' : 'light';
    }

    function applyPreference(preference, persist = false) {
        const theme = preference === 'auto' ? resolvedSystemTheme() : preference;
        const displayPreference = preference[0].toUpperCase() + preference.slice(1);
        const nextPreference = preferences[(preferences.indexOf(preference) + 1) % preferences.length];
        const nextDisplayPreference = nextPreference[0].toUpperCase() + nextPreference.slice(1);

        document.documentElement.dataset.themePreference = preference;
        document.documentElement.dataset.theme = theme;
        document.documentElement.style.colorScheme = theme;
        toggle.setAttribute(
            'aria-label',
            `Theme is ${displayPreference}${preference === 'auto' ? ` (${theme})` : ''}. Switch to ${nextDisplayPreference}.`
        );
        toggle.title = `Click to use ${nextDisplayPreference} theme`;
        if (label) {
            label.textContent = preference === 'auto'
                ? `Theme: Auto (${theme[0].toUpperCase() + theme.slice(1)})`
                : `Theme: ${displayPreference}`;
        }
        if (icon) icon.textContent = preference === 'auto' ? '\u25D0' : theme === 'light' ? '\u2600' : '\u263E';

        if (!persist) return;

        try {
            localStorage.setItem('avNetworkingTools:theme', preference);
        } catch (_error) {
            // The preference still works for this page when browser storage is unavailable.
        }
    }

    const initialPreference = preferences.includes(document.documentElement.dataset.themePreference)
        ? document.documentElement.dataset.themePreference
        : 'auto';
    applyPreference(initialPreference);

    toggle.addEventListener('click', () => {
        const currentPreference = document.documentElement.dataset.themePreference || 'dark';
        const nextPreference = preferences[(preferences.indexOf(currentPreference) + 1) % preferences.length];
        applyPreference(nextPreference, true);
    });

    const handleSystemThemeChange = () => {
        if (document.documentElement.dataset.themePreference === 'auto') applyPreference('auto');
    };

    if (systemPreference?.addEventListener) {
        systemPreference.addEventListener('change', handleSystemThemeChange);
    } else if (systemPreference?.addListener) {
        systemPreference.addListener(handleSystemThemeChange);
    }
})();

// Collapse navigation into a compact menu on narrow screens.
(function enableHeaderNavigation() {
    const navigation = document.querySelector('.header-actions');
    const toggle = navigation?.querySelector('.nav-menu-toggle');
    const moreMenu = navigation?.querySelector('.more-nav');
    if (!navigation || !toggle) return;

    function setMenuOpen(open) {
        navigation.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', String(open));
    }

    toggle.addEventListener('click', () => {
        setMenuOpen(!navigation.classList.contains('is-open'));
    });

    document.addEventListener('click', event => {
        if (navigation.contains(event.target)) return;
        setMenuOpen(false);
        if (moreMenu) moreMenu.open = false;
    });

    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        setMenuOpen(false);
        if (moreMenu) moreMenu.open = false;
        toggle.focus();
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth > 760) setMenuOpen(false);
    });
})();

// Offer devices from the most recent IP scan without restricting manual IP entry.
(function enableLastScanIpSuggestions() {
    const inputs = document.querySelectorAll('[data-ip-suggestions]');
    const suggestions = document.getElementById('lastScanIpSuggestions');
    const statusUrl = document.currentScript?.dataset.ipScanStatusUrl;

    if (!inputs.length || !suggestions || !statusUrl) return;

    inputs.forEach(input => input.setAttribute('list', suggestions.id));

    fetch(statusUrl, {headers: {'Accept': 'application/json'}})
        .then(response => {
            if (!response.ok) throw new Error('Could not load IP scan results.');
            return response.json();
        })
        .then(data => {
            const seen = new Set();
            const results = Array.isArray(data.results) ? data.results : [];

            results.forEach(result => {
                const ip = String(result?.ip || '').trim();
                if (!ip || seen.has(ip)) return;

                seen.add(ip);
                const option = document.createElement('option');
                option.value = ip;
                option.label = [result.hostname, result.manufacturer]
                    .map(value => String(value || '').trim())
                    .filter(value => value && value.toLowerCase() !== 'unknown')
                    .join(' - ');
                suggestions.appendChild(option);
            });
        })
        .catch(() => {
            // Inputs remain ordinary free-text fields if scan suggestions are unavailable.
        });
})();

// Keep unfinished field values when navigating between tool pages in this tab.
(function preservePageFields() {
    const storageKey = `avNetworkingTools:page-fields:${window.location.pathname}`;
    const fieldSelector = 'input:not([type="password"]):not([data-sensitive]):not([type="hidden"]):not([type="file"]):not([type="button"]):not([type="submit"]), select:not([data-sensitive]):not([data-no-draft]), textarea:not([data-sensitive])';
    // Remove password entries left by older versions, including drafts from other pages.
    try {
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            if (!key.startsWith('avNetworkingTools:page-fields:')) continue;
            const fields = JSON.parse(sessionStorage.getItem(key) || '{}');
            for (const name of Object.keys(fields)) {
                if (/password|secret|token/i.test(name)) delete fields[name];
            }
            sessionStorage.setItem(key, JSON.stringify(fields));
        }
    } catch (_error) { /* Storage may be unavailable. Never restore password controls. */ }
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
        if (field.closest(".priority-form")) return;
        // Adapter forms must reflect the live Windows configuration after navigation.
        if (field.closest(".config-form")) return;
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
            if (field.closest('.config-form')) return;
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
    const pendingLabel = form.querySelector('.mode-pending');
    if (pendingLabel) pendingLabel.hidden = form.dataset.modeChangePending !== 'true';
}

function syncNicForm(card, nic) {
    const form = card.querySelector('.config-form');
    if (!form) return;
    const modeSelect = form.querySelector('.mode-select');
    const currentMode = nic.dhcp_raw === 'Enabled' ? 'dhcp' : 'static';
    const forceSync = form.dataset.syncAfterRestore === 'true';
    delete form.dataset.syncAfterRestore;
    if (forceSync) {
        delete form.dataset.modeChangePending;
        delete form.dataset.editPending;
        modeSelect.value = currentMode;
    }
    form.dataset.appliedMode = currentMode;
    updateModeChangeState(form);
    if (form.dataset.modeChangePending === 'true' || form.dataset.editPending === 'true') return;

    modeSelect.value = currentMode;
    for (const [name, value] of Object.entries({
        ip: nic.ip || '', subnet: nic.subnet || '', gateway: nic.gateway || '',
        dns: (nic.dns || []).join(', ')
    })) {
        const field = form.querySelector(`[name="${name}"]`);
        if (field) field.value = value;
    }
    updateStaticVisibility(form);
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
    form.querySelectorAll('.static-fields input').forEach(field => {
        field.addEventListener('input', () => { form.dataset.editPending = 'true'; });
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
            form.dataset.editPending = 'true';
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
    notice.parentElement.querySelectorAll('.flash:not(#operationNotice)').forEach(item => item.remove());
}

function operationMessage(form) {
    if (form.classList.contains('priority-form')) return 'Updating IPv4 priority...';
    if (form.id.startsWith('release-')) return 'Releasing DHCP lease...';
    if (form.id.startsWith('restore-')) return 'Restoring previous network settings...';
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

document.querySelectorAll('.config-form, .priority-form, form[id^="release-"], form[id^="renew-"], form[id^="restore-"]').forEach(form => {
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (form.closest('.nic-card')?.classList.contains('is-busy')) return;
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
                delete form.dataset.editPending;
                updateModeChangeState(form);
            }
            if (form.id.startsWith('restore-') && operationSucceeded) {
                const configForm = busyCard?.querySelector('.config-form');
                if (configForm) {
                    delete configForm.dataset.modeChangePending;
                    delete configForm.dataset.editPending;
                    configForm.dataset.syncAfterRestore = 'true';
                    const pendingLabel = configForm.querySelector('.mode-pending');
                    if (pendingLabel) pendingLabel.hidden = true;
                }
            }
            hideNicBusyOverlay(busyCard);
            if (operationSucceeded) startNicRefreshBurst();
            else if (form.classList.contains('priority-form')) refreshNicStatus();
        };
        request.onerror = () => {
            hideNicBusyOverlay(busyCard);
            showOperationNotice('Network change failed. Please try again.', 'error');
        };
        request.send(new FormData(form));
    });
});

let nicRefreshInFlight = false;
let nicBurstRemaining = 0;
let nicBurstTimer;
let nicBurstGeneration = 0;

async function refreshNicStatus() {
    if (document.hidden || nicRefreshInFlight || document.querySelector('.nic-card.is-busy') ||
        document.body.dataset.updateInProgress === 'true') return;
    nicRefreshInFlight = true;
    try {
        const response = await fetch('/nics/status', {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || 'Could not refresh NICs.');
        // A network operation may have started while the status request was running.
        if (document.querySelector('.nic-card.is-busy')) return;
        for (const nic of data.nics) {
            const card = Array.from(document.querySelectorAll('.nic-card'))
                .find(item => item.dataset.interfaceIndex === String(nic.if_index));
            if (!card) continue;
            const status = card.querySelector('.status');
            status.className = `status ${nic.status}`;
            status.textContent = nic.status;
            const values = [nic.ip, nic.subnet, nic.gateway, (nic.dns || []).join(', '), nic.dhcp_raw, nic.link_status, nic.mac];
            card.querySelectorAll('.details > div:not(.label)').forEach((element, index) => {
                element.textContent = values[index] || '-';
            });
            const priority = card.querySelector('.priority-select');
            if (priority) {
                const value = nic.automatic_metric ? 'auto' : String(nic.metric ?? '');
                let option = Array.from(priority.options).find(item => item.value === value);
                priority.querySelectorAll('option[data-current], option[value=""]').forEach(item => item.remove());
                if (!option || !option.isConnected) {
                    option = document.createElement('option');
                    option.value = value;
                    option.textContent = nic.metric == null ? 'Unknown' : String(nic.metric);
                    option.disabled = true;
                    option.dataset.current = 'true';
                    priority.prepend(option);
                }
                priority.value = value;
            }
            card.querySelectorAll('.dhcp-action').forEach(button => { button.hidden = nic.dhcp_raw !== 'Enabled'; });
            syncNicForm(card, nic);
        }
    } catch (error) {
        // Keep the operation result visible even if a later status refresh fails.
        const control = document.getElementById('autoRefresh');
        if (control) control.title = error.message;
    } finally {
        nicRefreshInFlight = false;
    }
    return true;
}

function startNicRefreshBurst() {
    clearTimeout(nicBurstTimer);
    const generation = ++nicBurstGeneration;
    nicBurstRemaining = 6;
    async function tick() {
        if (!document.hidden) {
            const refreshed = await refreshNicStatus();
            if (generation !== nicBurstGeneration) return;
            if (refreshed) nicBurstRemaining--;
        }
        if (nicBurstRemaining > 0) nicBurstTimer = setTimeout(tick, 2000);
    }
    tick();
}

const autoRefresh = document.getElementById('autoRefresh');
if (autoRefresh) {
    try { autoRefresh.checked = localStorage.getItem('autoRefreshEnabled') !== 'false'; }
    catch (_) { autoRefresh.checked = true; }
    autoRefresh.addEventListener('change', () => {
        try { localStorage.setItem('autoRefreshEnabled', String(autoRefresh.checked)); } catch (_) {}
    });
    setInterval(() => {
        if (autoRefresh.checked && nicBurstRemaining === 0) refreshNicStatus();
    }, 10000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && autoRefresh.checked) refreshNicStatus();
    });
}

document.querySelectorAll('.priority-select').forEach(select => {
    select.addEventListener('change', () => select.form.requestSubmit());
});
