(() => {
    const dialog = document.getElementById('updateDialog');
    const message = document.getElementById('updateMessage');
    const status = document.getElementById('updateStatus');
    const progress = document.getElementById('updateProgress');
    const progressBar = document.getElementById('updateProgressBar');
    const updateNow = document.getElementById('updateNow');
    const updateLater = document.getElementById('updateLater');
    if (!dialog) return;

    let releaseInfo = null;
    let pollTimer = null;

    function showRelease(info) {
        releaseInfo = info;
        if (!info.available || sessionStorage.getItem('dismissedUpdateVersion') === info.latest_version) return;

        message.textContent = `Version ${info.latest_version} is available. You currently have version ${info.current_version}.`;
        status.textContent = info.error || (info.can_auto_update ? '' : 'Run the packaged EXE to install updates automatically.');
        updateNow.disabled = Boolean(info.error) || !info.can_auto_update;
        dialog.hidden = false;
    }

    async function checkForUpdate() {
        try {
            const cached = JSON.parse(sessionStorage.getItem('availableUpdate') || 'null');
            if (cached) {
                showRelease(cached);
                return;
            }
            const response = await fetch('/api/update/check', { cache: 'no-store' });
            const data = await response.json();
            if (!response.ok || !data.success) return;
            sessionStorage.setItem('availableUpdate', JSON.stringify(data));
            showRelease(data);
        } catch (_) {
            // Update checks must never interfere with normal app use.
        }
    }

    async function pollUpdateStatus() {
        try {
            const response = await fetch('/api/update/status', { cache: 'no-store' });
            const data = await response.json();
            status.textContent = data.message || 'Downloading update...';

            if (data.total_bytes > 0) {
                const percent = Math.min(100, Math.round(data.downloaded_bytes * 100 / data.total_bytes));
                progressBar.style.width = `${percent}%`;
            }

            if (data.status === 'ready') {
                clearInterval(pollTimer);
                const installResponse = await fetch('/api/update/install', { method: 'POST' });
                const installData = await installResponse.json();
                status.textContent = installData.message;
                if (!installResponse.ok) {
                    updateNow.disabled = false;
                    updateLater.disabled = false;
                    document.body.removeAttribute('data-update-in-progress');
                }
            } else if (data.status === 'error') {
                clearInterval(pollTimer);
                updateNow.disabled = false;
                updateLater.disabled = false;
                document.body.removeAttribute('data-update-in-progress');
            }
        } catch (_) {
            status.textContent = 'Could not read the update status. Please try again.';
            clearInterval(pollTimer);
            updateNow.disabled = false;
            updateLater.disabled = false;
            document.body.removeAttribute('data-update-in-progress');
        }
    }

    updateNow.addEventListener('click', async () => {
        updateNow.disabled = true;
        updateLater.disabled = true;
        progress.hidden = false;
        status.textContent = 'Starting download...';
        document.body.dataset.updateInProgress = 'true';

        try {
            const response = await fetch('/api/update/download', { method: 'POST' });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.message || 'Could not start the update.');
            status.textContent = data.message;
            pollTimer = setInterval(pollUpdateStatus, 750);
            pollUpdateStatus();
        } catch (error) {
            status.textContent = error.message;
            updateNow.disabled = false;
            updateLater.disabled = false;
            document.body.removeAttribute('data-update-in-progress');
        }
    });

    updateLater.addEventListener('click', () => {
        if (releaseInfo) sessionStorage.setItem('dismissedUpdateVersion', releaseInfo.latest_version);
        dialog.hidden = true;
    });

    checkForUpdate();
})();
