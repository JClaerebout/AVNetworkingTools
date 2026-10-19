(function () {
    document.addEventListener('keydown', event => {
        if (event.repeat || event.isComposing
            || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;

        if (event.key === 'Escape') {
            const stopButton = ['disconnectButton', 'stopScan', 'stopPing', 'stopMulticast', 'stopWifiScan', 'stopScript']
                .map(id => document.getElementById(id)).find(button => button && !button.disabled);
            if (stopButton) {
                event.preventDefault();
                stopButton.click();
            }
            return;
        }
        if (event.key !== 'Enter' || event.defaultPrevented) return;

        const target = event.target;
        if (target.closest('button, a, textarea, [contenteditable="true"], [role="dialog"], [role="menu"]')
            || target.matches('input[type="submit"], input[type="button"], input[type="file"]')) return;

        if (target.matches('#connHost, #connPort, .send-input, .send-name-input')) {
            const button = target.matches('#connHost, #connPort')
                ? document.getElementById('connectButton')
                : target.closest('.send-row')?.querySelector('.send-button');
            event.preventDefault();
            if (button && !button.disabled) button.click();
            return;
        }

        const startButton = ['startScan', 'startPing', 'startMulticast', 'startWifiScan']
            .map(id => document.getElementById(id)).find(Boolean);
        if (startButton) {
            event.preventDefault();
            if (!startButton.disabled) startButton.click();
            return;
        }

        let form = target.closest('.config-form');
        if (!form) {
            // Only fall back when the pending adapter is unambiguous.
            if (target.closest('form')) return;
            const pending = document.querySelectorAll('.config-form[data-edit-pending="true"], .config-form[data-mode-change-pending="true"]');
            if (pending.length === 1) form = pending[0];
        }
        if (!form) return;
        event.preventDefault();
        if (form.closest('.nic-card')?.classList.contains('is-busy')) return;
        const applyButton = form.querySelector('button[type="submit"]:not([form])');
        if (applyButton && !applyButton.disabled) form.requestSubmit(applyButton);
    });
})();
