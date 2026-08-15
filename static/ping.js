(function () {
    const urls = document.currentScript.dataset;

    const ipInput = document.getElementById("pingIp");
    const historySelect = document.getElementById("pingHistory");
    const startButton = document.getElementById("startPing");
    const stopButton = document.getElementById("stopPing");
    const downloadButton = document.getElementById("downloadPingTxt");
    const stateLabel = document.getElementById("pingState");
    const output = document.getElementById("pingOutput");

    if (!ipInput) return;

    historySelect.addEventListener("change", () => {
        if (historySelect.value) {
            ipInput.value = historySelect.value;
        }
    });

    function setOutput(lines) {
        output.textContent = lines && lines.length ? lines.join("\n") : "No output yet.";
        output.scrollTop = output.scrollHeight;
    }

    function updateHistory(history) {
        const selected = historySelect.value;
        historySelect.innerHTML = '<option value="">Select previous ping IP...</option>';
        for (const ip of history || []) {
            const option = document.createElement("option");
            option.value = ip;
            option.textContent = ip;
            if (ip === selected) option.selected = true;
            historySelect.appendChild(option);
        }
    }

    function renderStatus(data) {
        stateLabel.textContent = data.running ? `Running: ${data.target}` : "Stopped";
        startButton.disabled = data.running;
        stopButton.disabled = !data.running;
        downloadButton.disabled = !data.output || data.output.length === 0;
        setOutput(data.output);
        updateHistory(data.history);
    }

    async function refreshStatus() {
        const response = await fetch(urls.statusUrl);
        renderStatus(await response.json());
    }

    async function startPing() {
        const response = await fetch(urls.startUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ip: ipInput.value})
        });
        const data = await response.json();
        renderStatus(data);
        if (!data.success) alert(data.message);
    }

    async function stopPing() {
        const response = await fetch(urls.stopUrl, {method: "POST"});
        renderStatus(await response.json());
    }

    startButton.addEventListener("click", startPing);
    stopButton.addEventListener("click", stopPing);
    downloadButton.addEventListener("click", () => {
        window.location.href = urls.exportUrl;
    });

    refreshStatus();
    setInterval(refreshStatus, 1000);
})();
