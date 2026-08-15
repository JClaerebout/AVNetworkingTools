(function () {
    const urls = document.currentScript.dataset;

    const nicSelect = document.getElementById("scanNic");
    const customSubnetInput = document.getElementById("customSubnet");
    const quickScanCheckbox = document.getElementById("quickScan");
    const startScanButton = document.getElementById("startScan");
    const lookupDetailsButton = document.getElementById("lookupDetails");
    const stopScanButton = document.getElementById("stopScan");
    const scanStatus = document.getElementById("scanStatus");
    const scanResults = document.getElementById("scanResults");
    const downloadCsvButton = document.getElementById("downloadScanCsv");

    const scanFilter = document.getElementById("scanFilter");
    const clearScanFilter = document.getElementById("clearScanFilter");

    const contextMenu = document.getElementById("scanContextMenu");
    const copyIpButton = document.getElementById("copyIpButton");
    const copyMacButton = document.getElementById("copyMacButton");

    const monitorBox = document.getElementById("monitorBox");
    const monitorScanCheckbox = document.getElementById("monitorScan");

    if (!nicSelect) return;

    let latestResults = [];
    let selectedContextItem = null;
    let monitorStopping = false;

    function matchesFilter(item, filterText) {
        if (!filterText) return true;

        const text = [
            item.ip || "",
            item.mac || "",
            item.manufacturer || "",
            item.hostname || ""
        ].join(" ").toLowerCase();

        return text.includes(filterText.toLowerCase());
    }

    function renderResults(results) {
        latestResults = results || [];
        scanResults.innerHTML = "";

        const filterText = scanFilter.value.trim();
        const filteredResults = latestResults.filter(item => matchesFilter(item, filterText));

        if (latestResults.length === 0) {
            scanResults.innerHTML = '<tr><td colspan="5" class="muted-cell">No scan results yet.</td></tr>';
            return;
        }

        if (filteredResults.length === 0) {
            scanResults.innerHTML = '<tr><td colspan="5" class="muted-cell">No results match your search.</td></tr>';
            return;
        }

        for (const item of filteredResults) {
            const row = document.createElement("tr");
            row.className = "scan-result-row";

            row.dataset.ip = item.ip || "";
            row.dataset.mac = item.mac || "";

            let status = `<span class="status-pill ok">OK</span>`;

            if (item.is_local) {
                status = `<span class="status-pill local">This PC</span>`;
            } else if (item.missing) {
                status = `<span class="status-pill warn">Missing</span>`;
            } else if (item.duplicate_ip) {
                status = `<span class="status-pill danger">Duplicate IP</span>`;
            }

            row.classList.toggle("duplicate-ip-row", !!item.duplicate_ip);
            row.classList.toggle("missing-ip-row", !!item.missing);

            row.innerHTML = `
                <td>${item.ip || "-"}</td>
                <td>${item.mac || "-"}</td>
                <td>${item.manufacturer || "Unknown"}</td>
                <td>${item.hostname || "-"}</td>
                <td>${status}</td>
            `;

            row.addEventListener("contextmenu", event => {
                event.preventDefault();

                selectedContextItem = {
                    ip: row.dataset.ip,
                    mac: row.dataset.mac
                };

                showContextMenu(event.pageX, event.pageY);
            });

            scanResults.appendChild(row);
        }
    }

    function renderStatus(data) {
        let progress = "";

        if (data.running && data.total) {
            progress = ` (${data.done} / ${data.total})`;
        } else if (data.lookup_running && data.lookup_total) {
            progress = ` (${data.lookup_done} / ${data.lookup_total})`;
        }

        let monitorText = "";

        if (monitorStopping) {
            monitorText = " | Stopping monitor...";
        } else if (data.monitor_running) {
            monitorText = data.monitor_paused
                ? " | Monitor paused"
                : " | Monitor active";
        }

        scanStatus.textContent = `${data.message || "Idle"}${progress}${monitorText}`;

        startScanButton.disabled = data.running || data.lookup_running;
        lookupDetailsButton.style.display = data.can_lookup ? "" : "none";
        lookupDetailsButton.disabled = data.running || data.lookup_running;
        stopScanButton.disabled = !data.running && !data.lookup_running && !data.monitor_running;
        quickScanCheckbox.disabled = data.running || data.lookup_running;

        const scanFinished = !data.running && !data.lookup_running;
        const hasResults = data.results && data.results.length > 0;
        downloadCsvButton.disabled = !hasResults;
        const allowMonitor = !data.large_scan_quick_only;

        monitorBox.style.display = scanFinished && hasResults && allowMonitor ? "flex" : "none";
        if (!monitorStopping) {
            monitorScanCheckbox.checked =
                !!data.monitor_running && !data.monitor_paused;
        }
        if (monitorStopping && !data.monitor_running) {
            monitorStopping = false;
            monitorScanCheckbox.checked = false;
        }
        monitorScanCheckbox.disabled = data.running || data.lookup_running;

        renderResults(data.results);
    }

    async function refreshScanStatus() {
        const response = await fetch(urls.statusUrl);
        renderStatus(await response.json());
    }

    async function startScan() {
        const selectedNic = nicSelect.value;

        if (!selectedNic) {
            alert("Select a NIC first.");
            return;
        }

        const response = await fetch(urls.startUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                interface: selectedNic,
                custom_subnet: customSubnetInput.value.trim(),
                quick_scan: quickScanCheckbox.checked
            })
        });

        const data = await response.json();
        renderStatus(data);

        if (!data.success) {
            alert(data.message);
        }
    }

    async function stopScan() {
        const response = await fetch(urls.stopUrl, {
            method: "POST"
        });

        renderStatus(await response.json());
    }

    async function lookupDetails() {
        const response = await fetch(urls.lookupUrl, {
            method: "POST"
        });

        const data = await response.json();
        renderStatus(data);

        if (!data.success) {
            alert(data.message);
        }
    }

    async function setMonitor(enabled) {
        if (!enabled) {
            monitorStopping = true;
            monitorScanCheckbox.checked = false;
            scanStatus.textContent = "Stopping monitor...";
        }

        const url = enabled
            ? urls.monitorStartUrl
            : urls.monitorStopUrl;

        const response = await fetch(url, {
            method: "POST"
        });

        const data = await response.json();

        if (enabled) {
            monitorStopping = false;
        }

        renderStatus(data);

        if (!data.success) {
            alert(data.message);
        }
    }

    async function pauseMonitor(paused) {
        await fetch(urls.monitorPauseUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({paused})
        });
    }

    function showContextMenu(x, y) {
        contextMenu.style.left = `${x}px`;
        contextMenu.style.top = `${y}px`;
        contextMenu.style.display = "block";
    }

    function hideContextMenu() {
        contextMenu.style.display = "none";
    }

    async function copyText(value) {
        if (!value) return;

        try {
            await navigator.clipboard.writeText(value);
        } catch {
            const tempInput = document.createElement("input");
            tempInput.value = value;
            document.body.appendChild(tempInput);
            tempInput.select();
            document.execCommand("copy");
            tempInput.remove();
        }

        hideContextMenu();
    }

    startScanButton.addEventListener("click", startScan);
    lookupDetailsButton.addEventListener("click", lookupDetails);
    stopScanButton.addEventListener("click", stopScan);
    downloadCsvButton.addEventListener("click", () => {
        window.location.href = urls.exportUrl;
    });

    scanFilter.addEventListener("input", () => {
        renderResults(latestResults);
    });

    clearScanFilter.addEventListener("click", () => {
        scanFilter.value = "";
        renderResults(latestResults);
    });

    copyIpButton.addEventListener("click", () => {
        copyText(selectedContextItem?.ip || "");
    });

    copyMacButton.addEventListener("click", () => {
        copyText(selectedContextItem?.mac || "");
    });

    document.addEventListener("click", hideContextMenu);

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            hideContextMenu();
        }
    });

    monitorScanCheckbox.addEventListener("change", () => {
        setMonitor(monitorScanCheckbox.checked);
    });

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            pauseMonitor(true);
        } else {
            pauseMonitor(false);
            refreshScanStatus();
        }
    });

    window.addEventListener("pagehide", () => {
        pauseMonitor(true);
    });

    refreshScanStatus();
    setInterval(refreshScanStatus, 1000);
})();
