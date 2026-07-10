(function () {
    const urls = document.currentScript.dataset;

    const wifiStatus = document.getElementById("wifiStatus");
    const startWifiScanButton = document.getElementById("startWifiScan");
    const stopWifiScanButton = document.getElementById("stopWifiScan");
    const wifiList = document.getElementById("wifiList");

    if (!wifiStatus) return;

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, char => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
        }[char]));
    }

    function formatSignal(dbm, percent) {
        if (dbm === undefined || dbm === null) return "-";
        return `${dbm} dBm / ${percent ?? 0}%`;
    }

    function formatChannelLoad(value, source, assessment) {
        if (source === "reported" && value !== undefined && value !== null) {
            return `${value}%`;
        }
        if (source === "channel_reported" && value !== undefined && value !== null) {
            return `${value}% ch.`;
        }
        return assessment || "Unknown";
    }

    function formatDistance(value) {
        return value === undefined || value === null ? "-" : `~ ${value} m`;
    }

    function renderStatus(item) {
        const severity = item.status || item.severity || "ok";
        const statusClass = severity === "info" ? "weak" : severity;
        const label = item.status_label || item.severity_label || "OK";
        const reason = item.reason && item.reason !== "OK" ? ` title="${escapeHtml(item.reason)}"` : "";
        return `<span class="status-pill ${statusClass}"${reason}>${escapeHtml(label)}</span>`;
    }

    function renderList(groups) {
        const openSsids = new Set(
            Array.from(wifiList.querySelectorAll("details[open]"))
                .map(details => details.dataset.ssid)
                .filter(Boolean)
        );

        wifiList.innerHTML = "";

        if (!groups || groups.length === 0) {
            wifiList.innerHTML = '<div class="muted-cell">No WiFi results yet.</div>';
            return;
        }

        for (const group of groups) {
            const details = document.createElement("details");
            details.className = "wifi-entry";
            details.dataset.ssid = group.ssid || "";
            details.open = openSsids.has(details.dataset.ssid);

            const bands = (group.band_summary || []).join(", ");

            details.innerHTML = `
                <summary>
                    <span class="wifi-ssid">${escapeHtml(group.ssid)}</span>
                    <span class="wifi-pill">${escapeHtml(bands)}</span>
                    <span class="wifi-pill">Channels ${escapeHtml(group.channel_summary || "-")}</span>
                    <span class="wifi-pill">${group.radio_count || 0} radio(s)</span>
                    <span class="wifi-pill">${group.best_signal_dbm} dBm</span>
                    ${renderStatus(group)}
                </summary>

                <div class="table-wrap">
                    <table class="result-table">
                        <thead>
                            <tr>
                                <th>Access Point</th>
                                <th>MAC Address</th>
                                <th>Signal</th>
                                <th>Channel</th>
                                <th>Channel Width</th>
                                <th>Phy Mode</th>
                                <th>Channel Load</th>
                                <th>Clients</th>
                                <th>Distance</th>
                                <th>Security</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${(group.bssids || []).map(ap => `
                                <tr>
                                    <td>${escapeHtml(ap.ssid || group.ssid || "-")}</td>
                                    <td>${escapeHtml(ap.bssid || "-")}</td>
                                    <td>${formatSignal(ap.signal_dbm, ap.signal_percent)}</td>
                                    <td>${escapeHtml(ap.channel || "-")}</td>
                                    <td>${escapeHtml(ap.channel_width || "-")}</td>
                                    <td>${escapeHtml(ap.radio_type || "-")}</td>
                                    <td>${formatChannelLoad(ap.channel_load_percent, ap.channel_load_source, ap.channel_load_assessment)}</td>
                                    <td>${ap.connected_stations ?? "-"}</td>
                                    <td>${formatDistance(ap.distance_m)}</td>
                                    <td>${escapeHtml(ap.authentication || "-")} / ${escapeHtml(ap.encryption || "-")}</td>
                                    <td>${renderStatus(ap)}</td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </div>
            `;

            wifiList.appendChild(details);
        }
    }

    function renderWifiStatus(data) {
        wifiStatus.textContent = data.message || "Idle";
        startWifiScanButton.disabled = data.running;
        stopWifiScanButton.disabled = !data.running;

        const groups = data.results || [];
        renderList(groups);
    }

    async function refreshWifiStatus() {
        const response = await fetch(urls.statusUrl);
        renderWifiStatus(await response.json());
    }

    async function startWifiScan() {
        const response = await fetch(urls.startUrl, {
            method: "POST"
        });

        const data = await response.json();
        renderWifiStatus(data);

        if (!data.success) {
            alert(data.message);
        }
    }

    async function stopWifiScan() {
        const response = await fetch(urls.stopUrl, {
            method: "POST"
        });

        renderWifiStatus(await response.json());
    }

    startWifiScanButton.addEventListener("click", startWifiScan);
    stopWifiScanButton.addEventListener("click", stopWifiScan);

    refreshWifiStatus();
    setInterval(refreshWifiStatus, 1000);
})();
