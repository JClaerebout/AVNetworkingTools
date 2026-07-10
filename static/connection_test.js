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
    const outputBox = document.getElementById("connOutput");
    const autoScroll = document.getElementById("autoScroll");
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

    if (!protocolSelect) return;

    let lastOutput = "";

    function updateProtocolDefaults() {
        const protocol = protocolSelect.value;

        sshFields.style.display = protocol === "ssh" ? "grid" : "none";
        serialFields.style.display = protocol === "rs232" ? "grid" : "none";

        hostInput.closest("div").style.display = protocol === "rs232" ? "none" : "block";
        portInput.closest("div").style.display = protocol === "rs232" ? "none" : "block";

        if (protocol === "ssh" && (!portInput.value || portInput.value === "23")) portInput.value = "22";
        if (protocol === "telnet" && (!portInput.value || portInput.value === "22")) portInput.value = "23";

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
            data.running
                ? data.status_text
                : "Disconnected";
        connectButton.disabled = data.running;
        disconnectButton.disabled = !data.running;

        const newOutput = data.output && data.output.length
            ? data.output.map(formatOutputLine).join("\n\n")
            : "No data yet.";

        if (newOutput !== lastOutput) {
            const wasNearBottom =
                outputBox.scrollHeight - outputBox.scrollTop - outputBox.clientHeight < 20;

            const oldScrollTop = outputBox.scrollTop;

            outputBox.textContent = newOutput;

            if (autoScroll.checked && wasNearBottom) {
                outputBox.scrollTop = outputBox.scrollHeight;
            } else {
                outputBox.scrollTop = oldScrollTop;
            }

            lastOutput = newOutput;
        }
    }

    async function refreshStatus() {
        const response = await fetch(urls.statusUrl);
        renderStatus(await response.json());
    }

    async function connect() {
        const response = await fetch(urls.startUrl, {
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
        const data = await response.json();
        renderStatus(data);
        if (!data.success) alert(data.message);
    }

    async function disconnect() {
        const response = await fetch(urls.stopUrl, {method: "POST"});
        renderStatus(await response.json());
    }

    async function sendValue(input) {
        const response = await fetch(urls.sendUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                data: input.value,
                is_hex: sendAsHex.checked,
                add_cr: sendCr.checked,
                add_lf: sendLf.checked
            })
        });
        const data = await response.json();
        renderStatus(data);
        if (!data.success) alert(data.message);
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
        row.querySelector(".send-button").addEventListener("click", () => sendValue(input));
        row.querySelector(".remove-send-row").addEventListener("click", () => row.remove());
        input.addEventListener("keydown", event => {
            if (event.key === "Enter") sendValue(input);
        });
    }

    function addRow() {
        const row = document.createElement("div");
        row.className = "send-row";
        row.innerHTML = `
            <input class="send-input" placeholder="Extra command/data">
            <button class="btn send-button" type="button">Send</button>
            <button class="btn secondary remove-send-row" type="button">Remove</button>
        `;
        sendRows.appendChild(row);
        bindSendRow(row);
        row.querySelector(".send-input").focus();
    }

    function collectConnectionSettings(name, overwrite = false) {
        const sendFields = Array.from(document.querySelectorAll(".send-input"))
            .map(input => input.value);

        return {
            name: name,
            overwrite: overwrite,

            protocol: protocolSelect.value,
            host: hostInput.value,
            port: portInput.value,

            username: usernameInput.value,
            password: passwordInput.value,

            serial_port: serialPort.value,
            baudrate: serialBaudrate.value,
            databits: serialDatabits.value,
            parity: serialParity.value,
            stopbits: serialStopbits.value,

            send_fields: sendFields,

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

    function setSendFields(values) {
        sendRows.innerHTML = "";

        const list = values && values.length ? values : [""];

        list.forEach((value, index) => {
            const row = document.createElement("div");
            row.className = "send-row";

            row.innerHTML = `
                <input class="send-input" placeholder="Example: power on or \\x50\\x4F\\x57\\x0D">
                <button class="btn send-button" type="button">Send</button>
                <button class="btn secondary remove-send-row" type="button" ${index === 0 ? 'style="display:none;"' : ""}>Remove</button>
            `;

            sendRows.appendChild(row);
            row.querySelector(".send-input").value = value || "";
            bindSendRow(row);
        });
    }

    function applyConnectionSettings(entry) {
        protocolSelect.value = entry.protocol || "tcp";

        hostInput.value = entry.host || "";
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

        setSendFields(entry.send_fields || [""]);

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
                `A history entry named "${name}" already exists.\n\nPress OK to overwrite it.\nPress Cancel to choose another name.`
            );

            if (overwriteConfirm) {
                await saveCurrentConnectionHistory(true, name);
            } else {
                await saveCurrentConnectionHistory(false, "");
            }

            return;
        }

        if (!data.success) {
            alert(data.message || "Could not save history.");
            return;
        }

        await loadConnectionHistory();
        connectionHistory.value = name;
    }

    async function loadSelectedConnectionHistory() {
        const name = connectionHistory.value;
        if (!name) return;

        const response = await fetch(urls.historyEntryUrl.replace("__NAME__", encodeURIComponent(name)));
        const data = await response.json();

        if (!data.success) {
            alert(data.message || "Could not load history entry.");
            return;
        }

        applyConnectionSettings(data.entry);
    }

    protocolSelect.addEventListener("change", updateProtocolDefaults);
    connectButton.addEventListener("click", connect);
    disconnectButton.addEventListener("click", disconnect);
    addSendRow.addEventListener("click", addRow);
    rxAsHex.addEventListener("change", refreshStatus);
    refreshSerialPorts.addEventListener("click", loadSerialPorts);
    document.querySelectorAll(".send-row").forEach(bindSendRow);

    autoScroll.addEventListener("change", () => {
        if (autoScroll.checked) {
            outputBox.scrollTop = outputBox.scrollHeight;
        }
    });

    saveConnectionHistory.addEventListener("click", () => saveCurrentConnectionHistory());
    connectionHistory.addEventListener("change", loadSelectedConnectionHistory);

    updateProtocolDefaults();
    loadConnectionHistory();
    refreshStatus();
    setInterval(refreshStatus, 500);
})();
