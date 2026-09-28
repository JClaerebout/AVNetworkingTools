(() => {
    const url = document.currentScript.dataset.activityUrl;
    const container = document.getElementById('activeTasks');
    const label = document.getElementById('activeTasksLabel');
    const links = document.getElementById('activeTaskLinks');
    if (!container || !url) return;

    let timer;
    let inFlight = false;
    let stopped = false;
    let lastSignature = '';
    let lastTasks = [];

    function render(tasks, unavailable = false) {
        const signature = JSON.stringify([tasks, unavailable]);
        if (signature === lastSignature) return;
        lastSignature = signature;
        container.hidden = !unavailable && tasks.length === 0;
        container.classList.toggle('is-stale', unavailable);
        label.textContent = unavailable
            ? (tasks.length ? 'Activity unavailable (last known):' : 'Activity unavailable')
            : `Active (${tasks.length}):`;
        links.replaceChildren();
        for (const task of tasks) {
            if (typeof task.label !== 'string' || typeof task.url !== 'string' ||
                !task.url.startsWith('/') || task.url.startsWith('//')) continue;
            const link = document.createElement('a');
            link.className = 'active-task-link';
            link.href = task.url;
            link.textContent = task.label;
            links.appendChild(link);
        }
    }

    async function refresh() {
        if (stopped || inFlight || document.hidden) return;
        inFlight = true;
        clearTimeout(timer);
        try {
            const {response, data} = await window.AVRequests.jsonRequest(url, {}, 4000);
            if (!response.ok || !Array.isArray(data.tasks)) throw new Error('Activity status unavailable.');
            lastTasks = data.tasks;
            render(lastTasks);
        } catch (_error) {
            render(lastTasks, true);
        } finally {
            inFlight = false;
            if (!stopped) timer = setTimeout(refresh, 3000);
        }
    }

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refresh();
    });
    window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
    refresh();
})();
