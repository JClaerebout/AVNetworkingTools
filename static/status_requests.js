// Shared status polling for the live tool pages.
(() => {
    async function jsonRequest(url, options = {}, timeoutMs = 5000) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, {...options, signal: controller.signal});
            const data = await response.json();
            if (!data || typeof data !== 'object' || Array.isArray(data)) {
                throw new Error('Invalid server response.');
            }
            return {response, data};
        } catch (error) {
            if (controller.signal.aborted) throw new Error('Request timed out.');
            throw error;
        } finally {
            clearTimeout(timeout);
        }
    }

    function createStatusPoller({url, interval, onData, indicator}) {
        let pending = false;
        let actionPending = false;
        let generation = 0;
        let lastUpdated = null;
        let statusError = '';
        let actionError = '';
        let timer = null;
        let stopped = false;

        function showState() {
            const updated = lastUpdated ? `Last updated ${lastUpdated.toLocaleTimeString()}` : 'No update yet';
            indicator.textContent = [actionError, statusError, updated].filter(Boolean).join(' · ');
        }

        function schedule(delay = interval) {
            clearTimeout(timer);
            if (!stopped) timer = setTimeout(poll, delay);
        }

        async function poll() {
            if (stopped || pending || actionPending) return;
            pending = true;
            const current = generation;
            try {
                const {response, data} = await jsonRequest(url);
                if (current !== generation || stopped) return;
                if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
                onData(data);
                lastUpdated = new Date();
                statusError = '';
            } catch (error) {
                if (current === generation && !stopped) statusError = `Status unavailable: ${error.message}`;
            } finally {
                pending = false;
                if (!stopped) {
                    showState();
                    schedule();
                }
            }
        }

        async function action(actionUrl, options = {}) {
            if (actionPending) return null;
            actionPending = true;
            generation += 1; // A status response started before this action cannot overwrite it.
            clearTimeout(timer);
            try {
                const {response, data} = await jsonRequest(actionUrl, options);
                if (response.ok || Object.hasOwn(data, 'running') || Object.hasOwn(data, 'status_text')) {
                    onData(data);
                    lastUpdated = new Date();
                    statusError = '';
                }
                actionError = response.ok && data.success !== false
                    ? '' : `Action failed: ${data.message || `HTTP ${response.status}`}`;
                return data;
            } catch (error) {
                actionError = `Action failed: ${error.message}`;
                return null;
            } finally {
                actionPending = false;
                showState();
                schedule();
            }
        }

        showState();
        return {
            start() { stopped = false; poll(); },
            stop() { stopped = true; generation += 1; clearTimeout(timer); },
            poll,
            action
        };
    }

    window.AVRequests = {jsonRequest, createStatusPoller};
})();
