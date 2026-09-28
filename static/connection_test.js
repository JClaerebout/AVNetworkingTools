(function () {
    const urls = document.currentScript.dataset;

    const protocolSelect = document.getElementById("connProtocol");
    const hostInput = document.getElementById("connHost");
    const portInput = document.getElementById("connPort");
    const usernameInput = document.getElementById("connUsername");
    const passwordInput = document.getElementById("connPassword");
    const sshFields = document.getElementById("sshFields");
    const connectButton = document.getElementById("connectButton");
    const disconnectButton = document.getElementById("disconnectButton");
    const statusBox = document.getElementById("connStatus");
    const requestStatus = document.getElementById("connRequestStatus");
    const outputBox = document.getElementById("connOutput");
    const inlineInput = document.getElementById("connInlineInput");
    const autoScroll = document.getElementById("autoScroll");
    const logView = window.AVLogView.createLogView({
        output: outputBox,
        search: document.getElementById("connectionLogSearch"),
        count: document.getElementById("connectionLogCount"),
        latest: document.getElementById("connectionLogLatest"),
        separator: "\n\n",
        follow: () => autoScroll.checked
    });
    const exportButton = document.getElementById("exportConnectionTxt");
    const exportStatus = document.getElementById("connectionExportStatus");
    const sendRows = document.getElementById("sendRows");
    const addSendRow = document.getElementById("addSendRow");
    const sendAsHex = document.getElementById("sendAsHex");
    const rxAsHex = document.getElementById("rxAsHex");
    const sendCr = document.getElementById("sendCr");
    const sendLf = document.getElementById("sendLf");
    const serialFields = document.getElementById("serialFields");
    const serialPort = document.getElementById("serialPort");
    const refreshSerialPorts = document.getElementById("refreshSerialPorts");
    const serialBaudrate = document.getElementById("serialBaudrate");
    const serialDatabits = document.getElementById("serialDatabits");
    const serialParity = document.getElementById("serialParity");
    const serialStopbits = document.getElementById("serialStopbits");
    const connectionHistory = document.getElementById("connectionHistory");
    const saveConnectionHistory = document.getElementById("saveConnectionHistory");
    const presetHint = document.getElementById("connPresetHint");
    const suggestionSelect = document.getElementById("connSuggestion");

    if (!protocolSelect) return;

    const params = new URLSearchParams(window.location.search);
    const selectedTarget = params.get("target");
    const selectedManufacturer = params.get("manufacturer");
    const selectedHostname = params.get("hostname");
    if (selectedTarget) hostInput.value = selectedTarget;
    const matched = selectedTarget ? window.AVConnectionPresets.suggestions(selectedManufacturer, selectedHostname) : [];
    const learned = selectedTarget && urls.learnedProtocol && urls.learnedPort ? {
        id: "learned", label: `Learned for ${selectedManufacturer}`,
        protocol: urls.learnedProtocol, port: urls.learnedPort,
        note: "Saved after an earlier successful connection. Other models from this manufacturer may differ."
    } : null;
    const profiles = new Map();
    function addSuggestionGroup(label, items) {
        if (!items.length) return;
        const group = document.createElement("optgroup");
        group.label = label;
        items.forEach(profile => {
            profiles.set(profile.id, profile);
            const option = document.createElement("option");
            option.value = profile.id;
            option.textContent = `${profile.label} · ${profile.protocol.toUpperCase()} ${profile.port}`;
            group.appendChild(option);
        });
        suggestionSelect.appendChild(group);
    }
    addSuggestionGroup("Suggested for scanned device", [learned, ...matched].filter(Boolean));
    const matchedIds = new Set(matched.map(profile => profile.id));
    addSuggestionGroup("Other device profiles", window.AVConnectionPresets.all().filter(profile => !matchedIds.has(profile.id)));

    function applySuggestion(profile) {
        protocolSelect.value = profile.protocol;
        portInput.value = profile.port;
        updateProtocolDefaults();
        presetHint.textContent = `${profile.label}: ${profile.protocol.toUpperCase()} port ${profile.port}. ${profile.note}`
            + (profile.protocol === "udp" ? " Opening UDP does not confirm a device response." : "");
        presetHint.hidden = false;
    }
    const preferred = learned || matched[0];
    if (preferred) {
        suggestionSelect.value = preferred.id;
        applySuggestion(preferred);
    }
    if (params.has("manufacturer") || params.has("hostname")) {
        const cleanUrl = new URL(window.location.href);
        cleanUrl.searchParams.delete("manufacturer");
        cleanUrl.searchParams.delete("hostname");
        window.history.replaceState(null, "", cleanUrl);
    }

    function clearPresetHint() {
        presetHint.hidden = true;
        suggestionSelect.value = "";
    }

    const connectionConfigControls = [
        protocolSelect,
        hostInput,
        portInput,
        usernameInput,
        passwordInput,
        serialPort,
        refreshSerialPorts,
        serialBaudrate,
        serialDatabits,
        serialParity,
        serialStopbits,
        connectionHistory,
        suggestionSelect,
        saveConnectionHistory,
        connectButton
    ];

    let draggedSendRow = null;
    let recalledHistoryName = "";
    let exportInProgress = false;
    let exportStatusTimer = null;
    const sendRowsStorageKey = "avNetworkingTools:connection-send-rows";

    function saveSendRowsState() {
        const values = Array.from(sendRows.querySelectorAll(".send-row"))
            .map(row => ({
                command: row.querySelector(".send-input").value,
                name: row.querySelector(".send-name-input").value
            }));
        try {
            sessionStorage.setItem(sendRowsStorageKey, JSON.stringify(values));
        } catch (_error) {
            // The connection page remains usable when browser storage is unavailable.
        }
    }

    function loadSendRowsState() {
        try {
            const values = JSON.parse(sessionStorage.getItem(sendRowsStorageKey) || "null");
            if (!Array.isArray(values) || !values.length) return null;

            return values.map(value => typeof value === "string"
                ? {command: value, name: ""}
                : {command: value.command || "", name: value.name || ""});
        } catch (_error) {
            return null;
        }
    }

    function updateProtocolDefaults(applyPortDefault = false) {
        const protocol = protocolSelect.value;

        sshFields.style.display = protocol === "ssh" ? "grid" : "none";
        serialFields.style.display = protocol === "rs232" ? "grid" : "none";

        hostInput.closest("div").style.display = protocol === "rs232" ? "none" : "block";
        portInput.closest("div").style.display = protocol === "rs232" ? "none" : "block";

        if (applyPortDefault && protocol === "ssh" && (!portInput.value || portInput.value === "23")) portInput.value = "22";
        if (applyPortDefault && protocol === "telnet" && (!portInput.value || portInput.value === "22")) portInput.value = "23";

        if (protocol === "rs232") {
            loadSerialPorts();
        }
    }

    function formatOutputLine(item) {
        if (typeof item === "string") {
            return item;
        }

        if (item.direction === "TX") {
            const txValue = item.sent_as_hex ? item.hex : item.ascii;
            return `[${item.time}] TX\n${txValue}`;
        }

        if (item.direction === "RX") {
            const rxValue = rxAsHex.checked ? item.hex : item.ascii;
            return `[${item.time}] RX\n${rxValue}`;
        }

        return String(item);
    }

    function renderStatus(data) {
        statusBox.textContent =
            data.status_text || "Disconnected";
        connectionConfigControls.forEach(control => {
            control.disabled = data.running;
        });
        disconnectButton.disabled = !data.running;
        inlineInput.disabled = !(data.connected ?? data.running);
        exportButton.disabled = exportInProgress || !data.output || data.output.length === 0;

        logView.render((data.output || []).map(formatOutputLine), "No data yet.");
    }

    const poller = window.AVRequests.createStatusPoller({
        url: urls.statusUrl, interval: 500, onData: renderStatus, indicator: requestStatus
    });

    async function connect() {
        resetExportStatus();
        exportButton.disabled = true;
        const data = await poller.action(urls.startUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                protocol: protocolSelect.value,
                host: protocolSelect.value === "rs232" ? serialPort.value : hostInput.value,
                port: portInput.value,
                baudrate: serialBaudrate.value,
                databits: serialDatabits.value,
                parity: serialParity.value,
                stopbits: serialStopbits.value,
                username: usernameInput.value,
                password: passwordInput.value
            })
        });
        if (data && data.success) {
            inlineInput.focus();
            if (data.preset_learned || data.preset_warning) {
                presetHint.textContent = data.preset_warning || "Connected. Protocol and port saved as this manufacturer's local default; other models may differ.";
                presetHint.hidden = false;
            }
        }
    }

    async function disconnect() {
        await poller.action(urls.stopUrl, {method: "POST"});
    }

    function resetExportStatus() {
        clearTimeout(exportStatusTimer);
        exportStatus.textContent = "";
        exportStatus.classList.remove("success");
    }

    async function exportConnectionTxt() {
        exportInProgress = true;
        exportButton.disabled = true;
        try {
            const response = await fetch(urls.exportUrl, {method: "POST"});
            const data = await response.json();
            if (data.cancelled) { return; }
            if (!response.ok || !data.success) {
                alert(data.message || "Could not export connection session.");
                return;
            }

            resetExportStatus();
            exportStatus.textContent = "Saved to your selected file.";
            exportStatus.classList.add("success");
            exportStatusTimer = setTimeout(resetExportStatus, 4000);
        } catch (error) {
            alert(`Could not export connection session: ${error.message}`);
        } finally {
            exportInProgress = false;
            poller.poll();
        }
    }

    async function sendValue(input, clearAfterSend = false) {
        const value = input.value;
        const data = await poller.action(urls.sendUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                data: value,
                is_hex: sendAsHex.checked,
                add_cr: sendCr.checked,
                add_lf: sendLf.checked
            })
        });
        if (!data || !data.success) return;

        if (clearAfterSend && input.value === value) input.value = "";
    }

    async function loadSerialPorts() {
        const response = await fetch(urls.serialPortsUrl);
        const data = await response.json();

        serialPort.innerHTML = "";

        if (!data.ports || data.ports.length === 0) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "No COM ports found";
            serialPort.appendChild(option);
            return;
        }

        data.ports.forEach(port => {
            const option = document.createElement("option");
            option.value = port.device;
            option.textContent = `${port.device} - ${port.description}`;
            serialPort.appendChild(option);
        });
    }

    function bindSendRow(row) {
        const input = row.querySelector(".send-input");
        const nameInput = row.querySelector(".send-name-input");
        row.querySelector(".send-button").addEventListener("click", () => sendValue(input));
        row.querySelector(".remove-send-row").addEventListener("click", () => {
            row.remove();
            updateRemoveButtons();
            saveSendRowsState();
        });
        row.querySelector(".send-drag-handle").addEventListener("dragstart", event => {
            draggedSendRow = row;
            row.classList.add("is-dragging");
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", "send-row");
        });
        row.querySelector(".send-drag-handle").addEventListener("dragend", () => {
            row.classList.remove("is-dragging");
            draggedSendRow = null;
            saveSendRowsState();
        });
        input.addEventListener("input", saveSendRowsState);
        nameInput.addEventListener("input", saveSendRowsState);
        input.addEventListener("keydown", event => {
            if (event.key === "Enter") sendValue(input);
        });
    }

    function updateRemoveButtons() {
        const rows = Array.from(sendRows.querySelectorAll(".send-row"));
        rows.forEach(row => {
            row.querySelector(".remove-send-row").style.display = rows.length > 1 ? "" : "none";
        });
    }

    function sendRowAfterPointer(y) {
        const rows = Array.from(sendRows.querySelectorAll(".send-row:not(.is-dragging)"));

        return rows.reduce((closest, row) => {
            const box = row.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            return offset < 0 && offset > closest.offset ? {offset, row} : closest;
        }, {offset: Number.NEGATIVE_INFINITY, row: null}).row;
    }

    function addRow() {
        const row = document.createElement("div");
        row.className = "send-row";
        row.innerHTML = `
            <button class="send-drag-handle" type="button" draggable="true" aria-label="Drag to reorder command" title="Drag to reorder">&#9776;</button>
            <input class="send-input" placeholder="Extra command/data">
            <input class="send-name-input" placeholder="Optional command name" aria-label="Optional command name">
            <button class="btn send-button" type="button">Send</button>
            <button class="btn secondary remove-send-row" type="button">Remove</button>
        `;
        sendRows.appendChild(row);
        bindSendRow(row);
        updateRemoveButtons();
        saveSendRowsState();
        row.querySelector(".send-input").focus();
    }

    function collectConnectionSettings(name, overwrite = false) {
        const sendFieldRows = Array.from(sendRows.querySelectorAll(".send-row"));
        const sendFields = sendFieldRows.map(row => row.querySelector(".send-input").value);
        const sendFieldNames = sendFieldRows.map(row => row.querySelector(".send-name-input").value);

        return {
            name: name,
            overwrite: overwrite,

            protocol: protocolSelect.value,
            port: portInput.value,

            username: usernameInput.value,
            password: passwordInput.value,

            serial_port: serialPort.value,
            baudrate: serialBaudrate.value,
            databits: serialDatabits.value,
            parity: serialParity.value,
            stopbits: serialStopbits.value,

            send_fields: sendFields,
            send_field_names: sendFieldNames,

            send_as_hex: sendAsHex.checked,
            rx_as_hex: rxAsHex.checked,
            send_cr: sendCr.checked,
            send_lf: sendLf.checked,
            auto_scroll: autoScroll.checked
        };
    }

    async function loadConnectionHistory() {
        const response = await fetch(urls.historyUrl);
        const data = await response.json();

        connectionHistory.innerHTML = "";

        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = "Select saved setup...";
        connectionHistory.appendChild(emptyOption);

        if (!data.history || !data.history.length) return;

        data.history.forEach(item => {
            const option = document.createElement("option");
            option.value = item.name;
            option.textContent = item.name;
            connectionHistory.appendChild(option);
        });
    }

    function setSendFields(values, names = []) {
        sendRows.innerHTML = "";

        const list = values && values.length ? values : [""];

        list.forEach((value, index) => {
            const row = document.createElement("div");
            row.className = "send-row";

            row.innerHTML = `
                <button class="send-drag-handle" type="button" draggable="true" aria-label="Drag to reorder command" title="Drag to reorder">&#9776;</button>
                <input class="send-input" placeholder="Example: power on or \\x50\\x4F\\x57\\x0D">
                <input class="send-name-input" placeholder="Optional command name" aria-label="Optional command name">
                <button class="btn send-button" type="button">Send</button>
                <button class="btn secondary remove-send-row" type="button">Remove</button>
            `;

            sendRows.appendChild(row);
            row.querySelector(".send-input").value = value || "";
            row.querySelector(".send-name-input").value = names[index] || "";
            bindSendRow(row);
        });

        updateRemoveButtons();
        saveSendRowsState();
    }

    function applyConnectionSettings(entry) {
        clearPresetHint();
        protocolSelect.value = entry.protocol || "tcp";

        portInput.value = entry.port || "";

        usernameInput.value = entry.username || "";
        passwordInput.value = entry.password || "";

        serialBaudrate.value = entry.baudrate || "9600";
        serialDatabits.value = entry.databits || "8";
        serialParity.value = entry.parity || "N";
        serialStopbits.value = entry.stopbits || "1";

        sendAsHex.checked = !!entry.send_as_hex;
        rxAsHex.checked = !!entry.rx_as_hex;
        sendCr.checked = !!entry.send_cr;
        sendLf.checked = !!entry.send_lf;
        autoScroll.checked = entry.auto_scroll !== false;

        setSendFields(entry.send_fields || [""], entry.send_field_names || []);

        updateProtocolDefaults();

        setTimeout(() => {
            if (entry.serial_port) {
                serialPort.value = entry.serial_port;
            }
        }, 300);
    }

    async function saveCurrentConnectionHistory(overwrite = false, existingName = "") {
        const name = existingName || prompt("Save connection setup as:");

        if (!name) return;

        const payload = collectConnectionSettings(name, overwrite);

        const response = await fetch(urls.historySaveUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!data.success && data.message === "NAME_EXISTS") {
            const overwriteConfirm = confirm(
                `A saved setup named "${name}" already exists.\n\nPress OK to overwrite it.\nPress Cancel to choose another name.`
            );

            if (overwriteConfirm) {
                await saveCurrentConnectionHistory(true, name);
            } else {
                await saveCurrentConnectionHistory(false, "");
            }

            return;
        }

        if (!data.success) {
            alert(data.message || "Could not save setup.");
            return;
        }

        await loadConnectionHistory();
        connectionHistory.value = name;
        recalledHistoryName = name;
    }

    async function loadSelectedConnectionHistory() {
        const name = connectionHistory.value;
        if (!name) return;

        const response = await fetch(urls.historyEntryUrl.replace("__NAME__", encodeURIComponent(name)));
        const data = await response.json();

        if (!data.success) {
            alert(data.message || "Could not load setup.");
            return;
        }

        applyConnectionSettings(data.entry);
        recalledHistoryName = name;
    }

    async function saveConnectionHistoryFromButton() {
        if (!recalledHistoryName || connectionHistory.value !== recalledHistoryName) {
            await saveCurrentConnectionHistory();
            return;
        }

        const overwrite = confirm(
            `The saved setup "${recalledHistoryName}" is currently recalled.\n\n` +
            "Press OK to overwrite it.\nPress Cancel to save it as a new setup."
        );

        if (overwrite) {
            await saveCurrentConnectionHistory(true, recalledHistoryName);
        } else {
            await saveCurrentConnectionHistory(false, "");
        }
    }

    suggestionSelect.addEventListener("change", () => {
        const profile = profiles.get(suggestionSelect.value);
        if (profile) applySuggestion(profile);
        else presetHint.hidden = true;
    });
    protocolSelect.addEventListener("change", () => { clearPresetHint(); updateProtocolDefaults(true); });
    portInput.addEventListener("input", clearPresetHint);
    connectButton.addEventListener("click", connect);
    disconnectButton.addEventListener("click", disconnect);
    exportButton.addEventListener("click", exportConnectionTxt);
    addSendRow.addEventListener("click", addRow);
    inlineInput.addEventListener("keydown", event => {
        if (event.key !== "Enter" || event.isComposing) return;
        event.preventDefault();
        sendValue(inlineInput, true);
    });
    sendRows.addEventListener("dragover", event => {
        if (!draggedSendRow) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";

        const nextRow = sendRowAfterPointer(event.clientY);
        if (nextRow) {
            sendRows.insertBefore(draggedSendRow, nextRow);
        } else {
            sendRows.appendChild(draggedSendRow);
        }
    });
    sendRows.addEventListener("drop", event => {
        if (draggedSendRow) event.preventDefault();
    });
    rxAsHex.addEventListener("change", poller.poll);
    refreshSerialPorts.addEventListener("click", loadSerialPorts);
    const savedSendRows = loadSendRowsState();
    if (savedSendRows) {
        setSendFields(
            savedSendRows.map(row => row.command),
            savedSendRows.map(row => row.name)
        );
    } else {
        document.querySelectorAll(".send-row").forEach(bindSendRow);
        updateRemoveButtons();
    }

    autoScroll.addEventListener("change", () => {
        if (autoScroll.checked) {
            outputBox.scrollTop = outputBox.scrollHeight;
        }
    });

    saveConnectionHistory.addEventListener("click", saveConnectionHistoryFromButton);
    connectionHistory.addEventListener("change", () => {
        if (!connectionHistory.value) recalledHistoryName = "";
        loadSelectedConnectionHistory();
    });

    updateProtocolDefaults();
    loadConnectionHistory();
    poller.start();
    window.addEventListener("pagehide", poller.stop);
})();
