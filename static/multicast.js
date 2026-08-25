(function () {
    const urls = document.currentScript.dataset;
    const nic = document.getElementById("multicastNic");
    const status = document.getElementById("multicastStatus");
    const startButton = document.getElementById("startMulticast");
    const stopButton = document.getElementById("stopMulticast");
    const downloadButton = document.getElementById("downloadMulticastReport");
    const exportStatus = document.getElementById("multicastExportStatus");
    const groupsBody = document.getElementById("multicastGroups");

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, char => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
        }[char]));
    }

    function text(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    function renderGroups(groups) {
        if (!groups?.length) {
            groupsBody.innerHTML = '<tr><td colspan="7" class="muted-cell">No multicast traffic observed yet.</td></tr>';
            return;
        }
        groupsBody.innerHTML = groups.map(group => {
            const assessment = group.suspected_flood
                ? '<span class="status-pill danger">Flooding suspected</span>'
                : '<span class="status-pill ok">Normal</span>';
            return `<tr>
                <td>${escapeHtml(group.address)}</td>
                <td>${escapeHtml(group.service)}</td>
                <td>${Number(group.packets_per_second).toFixed(1)}</td>
                <td>${Number(group.mbps).toFixed(3)}</td>
                <td>${Number(group.packets).toLocaleString()}</td>
                <td>${group.membership_known ? (group.joined ? "Yes" : "No") : "Unknown"}</td>
                <td>${assessment}</td>
            </tr>`;
        }).join("");
    }

    function renderWarnings(warnings, running, elapsed) {
        const container = document.getElementById("multicastWarnings");
        if (warnings?.length) {
            container.innerHTML = warnings.map(item =>
                `<div class="diagnostic-warning ${escapeHtml(item.severity)}">&#9888; ${escapeHtml(item.message)}</div>`
            ).join("");
            return;
        }
        const message = running && elapsed < 130
            ? "No warnings so far. Querier evaluation completes after 130 seconds."
            : "No health warnings detected.";
        container.innerHTML = `<div class="diagnostic-ok">${escapeHtml(message)}</div>`;
    }

    function render(data) {
        status.textContent = data.message || "Idle";
        startButton.disabled = Boolean(data.running);
        stopButton.disabled = !data.running;
        downloadButton.hidden = Boolean(data.running) || !data.interface;
        downloadButton.disabled = Boolean(data.running) || !data.interface;
        nic.disabled = Boolean(data.running);
        if (data.interface && data.running) nic.value = data.interface;

        text("summaryInterface", data.interface || "-");
        text("summaryIp", data.ip || "-");
        text("summaryQuerier", data.querier_detected ? "YES" : "NO");
        text("summaryVersion", data.igmp_versions?.length ? data.igmp_versions.join(", ") : "Not observed");
        const counts = data.igmp_counts || {};
        text("summaryIgmpCounts", `${counts.query || 0} / ${counts.report || 0} / ${counts.leave || 0}`);
        text("summaryElapsed", `${Number(data.elapsed_seconds || 0).toFixed(1)} sec`);
        text("multicastTotalRate", `${Number(data.total_mbps || 0).toFixed(3)} Mbps`);

        const querierDetails = document.getElementById("querierDetails");
        if (data.queriers?.length) {
            querierDetails.innerHTML = data.queriers.map(item => {
                const interval = item.query_interval_seconds == null ? "measuring interval" : `~${item.query_interval_seconds} sec interval`;
                const queryAge = data.running
                    ? `last query ${item.last_query_seconds} sec ago`
                    : `last query ${item.last_query_seconds} sec before stop`;
                return `<div><strong>${escapeHtml(item.ip)}</strong> &mdash; ${queryAge}, ${interval}</div>`;
            }).join("");
        } else {
            querierDetails.textContent = "No query sources observed yet.";
        }
        renderWarnings(data.warnings, data.running, Number(data.elapsed_seconds || 0));
        renderGroups(data.groups);
    }

    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        const data = await response.json();
        render(data);
        return data;
    }

    startButton.addEventListener("click", async () => {
        const data = await fetchJson(urls.startUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({interface: nic.value})
        });
        if (!data.success) alert(data.message);
    });

    stopButton.addEventListener("click", () => fetchJson(urls.stopUrl, {method: "POST"}));

    downloadButton.addEventListener("click", async () => {
        downloadButton.disabled = true;
        exportStatus.textContent = "Saving report...";
        try {
            const response = await fetch(urls.exportUrl, {method: "POST"});
            const data = await response.json();
            if (!response.ok || !data.success) {
                alert(data.message || "Could not save multicast report.");
                exportStatus.textContent = "";
                return;
            }
            exportStatus.textContent = `Saved ${data.filename} to Downloads.`;
        } catch (error) {
            alert(`Could not save multicast report: ${error.message}`);
            exportStatus.textContent = "";
        } finally {
            downloadButton.disabled = false;
        }
    });

    async function refresh() {
        try {
            render(await (await fetch(urls.statusUrl)).json());
        } catch (_error) {
            status.textContent = "Could not refresh multicast status.";
        }
    }

    refresh();
    setInterval(refresh, 1000);
})();
