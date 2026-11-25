(function () {
    const urls = document.currentScript.dataset;
    const el = id => document.getElementById(id);
    const target = el('pingIp');
    const history = el('pingHistory');
    const mode = el('pingMode');
    const source = el('pingSource');
    const refresh = el('pingRefreshSources');
    const start = el('startPing');
    const stop = el('stopPing');
    const chart = el('pingChart');
    const storage = window.workspaceStorage || sessionStorage;
    const sourceKey = 'avNetworkingTools:page-fields:diagnostics-source';

    const numericFields = {
        interval: el('pingInterval'), timeout: el('pingTimeout'),
        max_hops: el('pingHopLimit'), hop_interval: el('pingHopInterval')
    };
    const logView = window.AVLogView.createLogView({
        output: el('pingOutput'), latest: el('pingLogLatest')
    });
    const selectedTarget = new URLSearchParams(window.location.search).get('target');
    if (selectedTarget) target.value = selectedTarget;

    const labels = {ping: 'Ping', trace: 'Traceroute', combined: 'Combined'};
    let status = {};
    let changing = false;
    let readingAdapters = false;
    let exportTimer;
    let lastHops = '';
    let lastHistory = '';
    let chosenSource = 'auto';
    try {
        const savedSource = JSON.parse(storage.getItem(sourceKey) || '{}').source;
        if (typeof savedSource === 'string' && savedSource) chosenSource = savedSource;
    } catch (_) { /* Continue with automatic routing when storage is unavailable. */ }

    function sourceOptions(sources) {
        const options = [new Option('Automatic routing', 'auto')];
        for (const adapter of sources) {
            const label = `${adapter.name} · ${adapter.ip}/${adapter.prefix}${adapter.secondary ? ' (secondary)' : ''}`;
            options.push(new Option(label, adapter.value));
        }
        if (!options.some(option => option.value === chosenSource)) {
            options.push(new Option(`Saved source unavailable · ${chosenSource.split('|').at(-1)}`, chosenSource));
        }
        source.replaceChildren(...options);
        source.value = chosenSource;
        source.selectedOptions[0].dataset.available = String(
            chosenSource === 'auto' || sources.some(adapter => adapter.value === chosenSource)
        );
    }

    function renderControls() {
        const locked = changing || !!status.running;
        const missingSource = source.selectedOptions[0]?.dataset.available === 'false';
        start.disabled = locked || (source.value !== 'auto' && (readingAdapters || missingSource));
        stop.disabled = changing || !status.running || status.stopping;
        el('clearPing').disabled = changing || (!status.running && !(status.mode || status.output?.length));
        for (const field of [target, history, mode, source, ...Object.values(numericFields)]) field.disabled = locked;
        refresh.disabled = locked || readingAdapters;
    }

    async function loadSources() {
        readingAdapters = true;
        el('pingSourceStatus').textContent = 'Reading active adapters…';
        renderControls();
        try {
            const {response, data} = await window.AVRequests.jsonRequest(urls.sourcesUrl, {}, 30000);
            if (!response.ok) throw new Error(data.message);
            sourceOptions(data.sources);
            el('pingSourceStatus').textContent = source.selectedOptions[0].dataset.available === 'false'
                ? 'Choose another source or refresh after reconnecting the adapter.'
                : (data.sources.length ? '' : 'No active adapter has a usable IPv4 address.');
        } catch (_) {
            el('pingSourceStatus').textContent = 'Adapter addresses unavailable. Refresh to try again.';
        } finally {
            readingAdapters = false;
            renderControls();
        }
    }

    function configureMode() {
        el('pingIntervalField').hidden = mode.value === 'trace';
        el('pingHopLimitField').hidden = mode.value === 'ping';
        el('pingHopIntervalField').hidden = mode.value !== 'combined';
        start.textContent = `Start ${labels[mode.value]}`;
    }

    function number(value) {
        return Number.isFinite(value) ? value.toFixed(2) : '—';
    }

    function unit(value, suffix) {
        return Number.isFinite(value) ? `${number(value)}${suffix}` : '—';
    }

    function hopLabel(hop) {
        if (!hop.address) return '*';
        return hop.address === status.destination && status.target !== status.destination
            ? `${hop.address} (${status.target})` : hop.address;
    }

    function renderRoute() {
        const hops = status.hops || [];
        const key = JSON.stringify([status.target, status.destination, hops]);
        if (key === lastHops) return;
        lastHops = key;
        const rows = hops.map(hop => {
            const row = document.createElement('tr');
            const values = [
                hop.hop, hopLabel(hop), number(hop.loss), hop.sent, hop.received,
                hop.kind === 'timeout' ? '*' : number(hop.last),
                number(hop.avg), number(hop.best), number(hop.worst)
            ];
            values.forEach((value, index) => {
                const cell = row.insertCell();
                cell.textContent = value;
                if (index === 1 && hop.kind === 'unreachable') {
                    cell.append(' · unreachable');
                    cell.classList.add('diagnostics-result-failed');
                }
                if ((index === 1 && value === '*') || (index === 5 && value === '*')) {
                    cell.classList.add('diagnostics-result-timeout');
                }
            });
            return row;
        });
        el('pingHops').replaceChildren(...rows);
    }

    function drawChart() {
        if (el('pingChartPanel').hidden || !chart.clientWidth) return;
        const values = status.samples || [];
        const width = chart.clientWidth, height = chart.clientHeight;
        const scale = window.devicePixelRatio || 1;
        const context = chart.getContext('2d');
        if (!context) return;
        chart.width = Math.round(width * scale);
        chart.height = Math.round(height * scale);
        context.scale(scale, scale);
        const theme = getComputedStyle(chart);
        const peak = Math.max(1, ...values.filter(Number.isFinite));
        const left = 59, top = 12, bottom = height - 19;
        context.font = '11px system-ui';
        context.fillStyle = theme.getPropertyValue('--muted');
        context.strokeStyle = theme.getPropertyValue('--line');
        for (const ratio of [0, 0.5, 1]) {
            const y = bottom - ratio * (bottom - top);
            context.fillText(`${(peak * ratio).toFixed(peak < 10 ? 1 : 0)} ms`, 0, y + 4);
            context.beginPath();
            context.moveTo(left, y);
            context.lineTo(width - 8, y);
            context.stroke();
        }
        context.strokeStyle = context.fillStyle = theme.getPropertyValue('--accent');
        context.lineWidth = 2;
        const points = values.map((value, index) => [
            left + index * (width - left - 12) / Math.max(1, values.length - 1),
            bottom - value / peak * (bottom - top)
        ]);
        context.beginPath();
        values.forEach((value, index) => {
            if (!Number.isFinite(value)) return;
            if (Number.isFinite(values[index - 1])) context.lineTo(...points[index]);
            else context.moveTo(...points[index]);
        });
        context.stroke();
        values.forEach((value, index) => {
            if (Number.isFinite(value) && !Number.isFinite(values[index - 1]) && !Number.isFinite(values[index + 1])) {
                context.beginPath();
                context.arc(...points[index], 2.5, 0, 2 * Math.PI);
                context.fill();
            }
        });
    }

    function renderStatus(data) {
        status = data;
        const activeMode = data.mode || mode.value;
        el('pingResultsHeading').textContent = data.target
            ? `${labels[activeMode]} results · ${data.target}` : 'Results';
        el('pingState').textContent = data.stopping ? 'Stopping…' : (data.message || 'Ready');
        const routeNote = el('pingRouteNote');
        const route = data.route || {};
        const origin = route.source ? `${route.source}${route.prefix != null ? `/${route.prefix}` : ''}` : '';
        routeNote.textContent = [origin, route.interface, route.note,
            route.default_gateway ? `Adapter default gateway ${route.default_gateway}.` : ''].filter(Boolean).join(' · ');
        routeNote.hidden = !routeNote.textContent;
        const stats = data.stats || {};
        for (const field of ['Last', 'Best', 'Avg', 'Worst']) {
            el(`ping${field}`).textContent = unit(stats[field.toLowerCase()], ' ms');
        }
        el('pingLoss').textContent = unit(stats.loss, '%');
        el('pingSent').textContent = stats.sent ?? '—';
        el('pingReceived').textContent = stats.received ?? '—';
        el('pingHopCount').textContent = activeMode === 'ping' ? '—'
            : (data.hops || []).filter(hop => hop.address).length;
        const reachability = el('pingReachability');
        reachability.textContent = data.reachability || '—';
        reachability.classList.toggle('diagnostics-result-reached', data.reachability === 'Reached');
        reachability.classList.toggle('diagnostics-result-failed', data.reachability === 'Unreachable');
        reachability.classList.toggle('diagnostics-result-timeout', data.reachability === 'No reply');
        el('pingChartPanel').hidden = activeMode === 'trace' || !data.samples?.length;
        el('pingRoutePanel').hidden = activeMode === 'ping';
        el('pingProgress').textContent = data.progress
            ? `Hop ${data.progress} of up to ${data.max_hops}${activeMode === 'combined'
                ? ` · ${data.sweeps} sweeps · ${data.route_changes} route changes` : ''}`
            : (data.running ? 'Waiting for the first hop…' : 'No route discovered.');
        const copy = el('copyPingRoute');
        copy.hidden = activeMode === 'ping';
        copy.disabled = !data.hops?.length;
        el('downloadPingTxt').disabled = !(data.mode || data.output?.length);
        el('downloadPingCsv').disabled = !data.mode;
        renderRoute();
        drawChart();
        logView.render(data.output || [], 'No output yet.');
        const historyKey = JSON.stringify(data.history || []);
        if (lastHistory !== historyKey) {
            lastHistory = historyKey;
            const selected = history.value;
            history.replaceChildren(new Option('Select previous target...', ''),
                ...(data.history || []).map(ip => new Option(ip, ip)));
            history.value = [...history.options].some(option => option.value === selected) ? selected : '';
        }
        renderControls();
    }

    const poller = window.AVRequests.createStatusPoller({
        url: urls.statusUrl, interval: 600, onData: renderStatus, indicator: el('pingRequestStatus')
    });

    async function action(url, data) {
        changing = true;
        renderControls();
        try {
            const options = {method: 'POST'};
            if (data) {
                options.headers = {'Content-Type': 'application/json'};
                options.body = JSON.stringify(data);
            }
            await poller.action(url, options);
        } finally {
            changing = false;
            renderControls();
        }
    }

    function showExportStatus(message, success) {
        const indicator = el('pingExportStatus');
        clearTimeout(exportTimer);
        indicator.textContent = message;
        indicator.classList.toggle('success', success);
        exportTimer = setTimeout(() => {
            indicator.textContent = '';
            indicator.classList.remove('success');
        }, 4000);
    }

    async function exportReport(url, format) {
        try {
            const response = await fetch(url, {method: 'POST'});
            const data = await response.json();
            if (data.cancelled) return;
            if (!response.ok || !data.success) throw new Error(data.message || `Could not save ${format}.`);
            showExportStatus('Saved to your selected file.', true);
        } catch (error) {
            showExportStatus(error.message, false);
        }
    }

    el('copyPingRoute').addEventListener('click', async () => {
        const lines = (status.hops || []).map(hop =>
            `${hop.hop}\t${hopLabel(hop)}\t${hop.kind === 'timeout' ? '*' : unit(hop.last, ' ms')}${
                hop.kind === 'unreachable' ? ' (unreachable)' : ''}`);
        try {
            await navigator.clipboard.writeText([`Route to ${status.target}`, 'Hop\tAddress\tLast reply', ...lines].join('\n'));
            showExportStatus('Route copied.', true);
        } catch (_) {
            showExportStatus('Clipboard unavailable. Use Download TXT to save the route.', false);
        }
    });

    history.addEventListener('change', () => {
        if (history.value) {
            target.value = history.value;
            target.dispatchEvent(new Event('change', {bubbles: true}));
        }
    });
    source.addEventListener('change', () => {
        chosenSource = source.value;
        try { storage.setItem(sourceKey, JSON.stringify({source: chosenSource})); }
        catch (_) { /* Selection still applies to the current run. */ }
        renderControls();
    });
    mode.addEventListener('change', () => {
        configureMode();
        renderStatus(status);
    });
    refresh.addEventListener('click', loadSources);
    start.addEventListener('click', () => {
        const data = {ip: target.value, mode: mode.value, source: source.value};
        for (const [key, field] of Object.entries(numericFields)) {
            if (field.parentElement.hidden) continue;
            if (!field.reportValidity()) return;
            data[key] = field.valueAsNumber;
        }
        action(urls.startUrl, data);
    });
    stop.addEventListener('click', () => action(urls.stopUrl));
    el('clearPing').addEventListener('click', () => action(urls.clearUrl));
    el('downloadPingTxt').addEventListener('click', () => exportReport(urls.exportUrl, 'TXT'));
    el('downloadPingCsv').addEventListener('click', () => exportReport(urls.csvUrl, 'CSV'));
    new ResizeObserver(drawChart).observe(chart);

    sourceOptions([]);
    configureMode();
    renderStatus({history: [...history.options].slice(1).map(option => option.value)});
    loadSources();
    poller.start();
    window.addEventListener('pagehide', poller.stop);
})();
