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
    const downloadMonitorLogButton = document.getElementById("downloadMonitorLog");
    const exportStatus = document.getElementById("scanExportStatus");

    const scanFilter = document.getElementById("scanFilter");
    const clearScanFilter = document.getElementById("clearScanFilter");

    const contextMenu = document.getElementById("scanContextMenu");
    const openWebpageButton = document.getElementById("openWebpageButton");
    const pingDeviceButton = document.getElementById("pingDeviceButton");
    const connectDeviceButton = document.getElementById("connectDeviceButton");
    const addToScriptButton = document.getElementById("addToScriptButton");
    const copyIpButton = document.getElementById("copyIpButton");
    const copyMacButton = document.getElementById("copyMacButton");
    const copyHostnameButton = document.getElementById("copyHostnameButton");
    const copyManufacturerButton = document.getElementById("copyManufacturerButton");

    const monitorBox = document.getElementById("monitorBox");
    const monitorScanCheckbox = document.getElementById("monitorScan");

    if (!nicSelect) return;

    let latestResults = [];
    let selectedContextItem = null;
    let monitorStopping = false;
    let exportStatusTimer = null;
    let menuTriggerIp = null;

    function actionButtonFor(ip) {
        return Array.from(scanResults.querySelectorAll(".scan-result-row"))
            .find(row => row.dataset.ip === ip)?.querySelector(".scan-actions-button");
    }

    function showExportSuccess(message) {
        clearTimeout(exportStatusTimer);
        exportStatus.textContent = message;
        exportStatus.classList.add("success");
        exportStatusTimer = setTimeout(() => {
            exportStatus.textContent = "";
            exportStatus.classList.remove("success");
        }, 4000);
    }

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
        const focusedIp = document.activeElement?.classList?.contains("scan-actions-button")
            ? document.activeElement.closest(".scan-result-row")?.dataset.ip : null;
        latestResults = results || [];
        scanResults.innerHTML = "";

        const filterText = scanFilter.value.trim();
        const filteredResults = latestResults.filter(item => matchesFilter(item, filterText));
        downloadCsvButton.disabled = filteredResults.length === 0;

        if (latestResults.length === 0) {
            scanResults.innerHTML = '<tr><td colspan="7" class="muted-cell">No scan results yet.</td></tr>';
            return;
        }

        if (filteredResults.length === 0) {
            scanResults.innerHTML = '<tr><td colspan="7" class="muted-cell">No results match your search.</td></tr>';
            return;
        }

        for (const item of filteredResults) {
            const row = document.createElement("tr");
            row.className = "scan-result-row";

            row.dataset.ip = item.ip || "";
            row.dataset.mac = item.mac || "";
            row.dataset.hostname = item.hostname || "";
            row.dataset.manufacturer = item.manufacturer || "";
            row.dataset.webScheme = !item.missing && item.web_services?.includes("https")
                ? "https"
                : !item.missing && item.web_services?.includes("http") ? "http" : "";

            let status = `<span class="status-pill ok">OK</span>`;

            if (item.is_local) {
                status = `<span class="status-pill local">This PC</span>`;
            } else if (item.missing) {
                status = `<span class="status-pill warn">Missing</span>`;
            } else if (item.duplicate_ip) {
                status = `<span class="status-pill danger" title="Repeated MAC changes observed recently; inspect the monitor log.">Possible IP conflict</span>`;
            }

            row.classList.toggle("duplicate-ip-row", !!item.duplicate_ip);
            row.classList.toggle("missing-ip-row", !!item.missing);

            const webScheme = row.dataset.webScheme;
            const webLabel = webScheme ? `${webScheme.toUpperCase()} web interface available` : "";
            const ipCell = webScheme
                ? `<button class="scan-web-link" type="button" title="Open ${webScheme.toUpperCase()} webpage">${item.ip || "-"}</button>`
                : item.ip || "-";

            row.innerHTML = `
                <td class="web-column">${webScheme ? `<span class="web-available" title="${webLabel}" aria-label="${webLabel}">&#10003;</span>` : ""}</td>
                <td>${ipCell}</td>
                <td>${item.mac || "-"}</td>
                <td>${item.manufacturer || "Unknown"}</td>
                <td>${item.hostname || "-"}</td>
                <td>${status}</td>
                <td><button class="scan-actions-button" type="button" aria-haspopup="menu" aria-controls="scanContextMenu" aria-expanded="false">Actions</button></td>
            `;
            row.querySelector(".scan-actions-button").setAttribute("aria-label", `Actions for ${row.dataset.ip || "device"}`);

            row.querySelector(".scan-web-link")?.addEventListener("click", event => {
                event.stopPropagation();
                openWebpage(row.dataset.ip, row.dataset.webScheme);
            });

            function selectRow() {
                selectedContextItem = {
                    ip: row.dataset.ip,
                    mac: row.dataset.mac,
                    hostname: row.dataset.hostname,
                    manufacturer: row.dataset.manufacturer,
                    webScheme: row.dataset.webScheme
                };
            }
            row.querySelector(".scan-actions-button").addEventListener("click", event => {
                event.stopPropagation();
                selectRow();
                const bounds = event.currentTarget.getBoundingClientRect();
                showContextMenu(bounds.left, bounds.bottom, row.dataset.ip);
            });
            row.addEventListener("contextmenu", event => {
                event.preventDefault();
                selectRow();
                showContextMenu(event.clientX, event.clientY, row.dataset.ip);
            });

            scanResults.appendChild(row);
        }
        if (focusedIp) actionButtonFor(focusedIp)?.focus({preventScroll: true});
        if (menuTriggerIp && !actionButtonFor(menuTriggerIp)) hideContextMenu();
        else if (menuTriggerIp) actionButtonFor(menuTriggerIp)?.setAttribute("aria-expanded", "true");
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
        downloadMonitorLogButton.disabled = data.monitor_running || !data.monitor_log_available;
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

        downloadMonitorLogButton.disabled = true;
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

    function menuButtons() {
        return Array.from(contextMenu.querySelectorAll("button"))
            .filter(button => !button.disabled && button.style.display !== "none");
    }

    function showContextMenu(x, y, triggerIp) {
        if (menuTriggerIp) actionButtonFor(menuTriggerIp)?.setAttribute("aria-expanded", "false");
        menuTriggerIp = triggerIp;
        actionButtonFor(triggerIp)?.setAttribute("aria-expanded", "true");
        const hasIp = !!selectedContextItem?.ip;
        pingDeviceButton.disabled = !hasIp;
        connectDeviceButton.disabled = !hasIp;
        addToScriptButton.disabled = !hasIp;
        const scheme = selectedContextItem?.webScheme || "";
        copyHostnameButton.disabled = !isCopyableDetail(selectedContextItem?.hostname);
        copyManufacturerButton.disabled = !isCopyableDetail(selectedContextItem?.manufacturer);
        openWebpageButton.style.display = scheme ? "" : "none";
        openWebpageButton.textContent = scheme
            ? `Open webpage (${scheme.toUpperCase()})`
            : "Open webpage";
        contextMenu.style.display = "block";
        const margin = 8;
        contextMenu.style.maxHeight = `${window.innerHeight - margin * 2}px`;
        contextMenu.style.overflowY = "auto";
        const bounds = contextMenu.getBoundingClientRect();
        contextMenu.style.left = `${Math.max(margin, Math.min(x, window.innerWidth - bounds.width - margin))}px`;
        contextMenu.style.top = `${Math.max(margin, Math.min(y, window.innerHeight - bounds.height - margin))}px`;
        menuButtons()[0]?.focus();
    }

    function isCopyableDetail(value) {
        const normalized = (value || "").trim().toLowerCase();
        return normalized !== ""
            && normalized !== "-"
            && normalized !== "unknown"
            && normalized !== "looking up...";
    }

    function hideContextMenu(restoreFocus = false) {
        const trigger = menuTriggerIp ? actionButtonFor(menuTriggerIp) : null;
        trigger?.setAttribute("aria-expanded", "false");
        contextMenu.style.display = "none";
        menuTriggerIp = null;
        if (restoreFocus) trigger?.focus();
    }

    function openTool(baseUrl) {
        const ip = selectedContextItem?.ip;
        if (!ip) return;
        const url = new URL(baseUrl, window.location.href);
        url.searchParams.set("target", ip);
        window.location.assign(url.href);
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

    async function openWebpage(ip, scheme) {
        if (!ip || !scheme) return;

        hideContextMenu();
        try {
            const response = await fetch(urls.openWebUrl, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ip, scheme})
            });
            const data = await response.json();
            if (data.cancelled) { return; }
            if (!response.ok || !data.success) {
                alert(data.message || "Could not open webpage.");
            }
        } catch (error) {
            alert(`Could not open webpage: ${error.message}`);
        }
    }

    async function saveVisibleResultsCsv() {
        const filterText = scanFilter.value.trim();
        const visibleResults = latestResults.filter(item => matchesFilter(item, filterText));
        if (!visibleResults.length) return;

        downloadCsvButton.disabled = true;
        try {
            const response = await fetch(urls.exportUrl, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({results: visibleResults})
            });
            const data = await response.json();
            if (data.cancelled) { return; }
            if (!response.ok || !data.success) {
                alert(data.message || "Could not save CSV.");
                return;
            }
            showExportSuccess(`Saved ${data.count} visible result${data.count === 1 ? "" : "s"} to your selected file.`);
        } catch (error) {
            alert(`Could not save CSV: ${error.message}`);
        } finally {
            renderResults(latestResults);
        }
    }

    async function saveMonitorLog() {
        downloadMonitorLogButton.disabled = true;
        try {
            const response = await fetch(urls.monitorExportUrl, {method: "POST"});
            const data = await response.json();
            if (data.cancelled) { return; }
            if (!response.ok || !data.success) {
                alert(data.message || "Could not save monitor log.");
                return;
            }
            showExportSuccess(`Saved monitor log (${data.count} entries) to your selected file.`);
        } catch (error) {
            alert(`Could not save monitor log: ${error.message}`);
        } finally {
            refreshScanStatus();
        }
    }

    startScanButton.addEventListener("click", startScan);
    lookupDetailsButton.addEventListener("click", lookupDetails);
    stopScanButton.addEventListener("click", stopScan);
    downloadCsvButton.addEventListener("click", saveVisibleResultsCsv);
    downloadMonitorLogButton.addEventListener("click", saveMonitorLog);

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
    pingDeviceButton.addEventListener("click", () => openTool(urls.pingUrl));
    connectDeviceButton.addEventListener("click", () => openTool(urls.connectUrl));
    addToScriptButton.addEventListener("click", () => openTool(urls.scriptsUrl));

    openWebpageButton.addEventListener("click", event => {
        event.stopPropagation();
        openWebpage(
            selectedContextItem?.ip || "",
            selectedContextItem?.webScheme || ""
        );
    });

    copyMacButton.addEventListener("click", () => {
        copyText(selectedContextItem?.mac || "");
    });

    copyHostnameButton.addEventListener("click", () => {
        copyText(selectedContextItem?.hostname || "");
    });

    copyManufacturerButton.addEventListener("click", () => {
        copyText(selectedContextItem?.manufacturer || "");
    });

    document.addEventListener("click", event => {
        if (!contextMenu.contains(event.target)) hideContextMenu();
    });

    document.addEventListener("keydown", event => {
        if (contextMenu.style.display !== "block") return;
        if (event.key === "Escape") {
            event.preventDefault();
            hideContextMenu(true);
        } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && contextMenu.contains(document.activeElement)) {
            event.preventDefault();
            const buttons = menuButtons();
            const index = buttons.indexOf(document.activeElement);
            const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
                : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next]?.focus();
        }
    });
    document.addEventListener("focusin", event => {
        if (contextMenu.style.display === "block" && !contextMenu.contains(event.target)
            && event.target !== actionButtonFor(menuTriggerIp)) hideContextMenu();
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
