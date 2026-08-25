(function () {
    const urls = document.currentScript.dataset;
    const canvas = document.getElementById("scriptCanvas");
    const emptyState = document.getElementById("scriptEmpty");
    const statusBox = document.getElementById("scriptStatus");
    const output = document.getElementById("scriptOutput");
    const runButton = document.getElementById("runScript");
    const pauseButton = document.getElementById("pauseScript");
    const stopButton = document.getElementById("stopScript");
    const savedSelect = document.getElementById("savedScriptSelect");
    const loadButton = document.getElementById("loadScript");
    const deleteButton = document.getElementById("deleteScript");
    const saveStatus = document.getElementById("scriptSaveStatus");
    const storageKey = "avNetworkingTools:scriptDraft:v1";
    let draggedBlock = null;
    let lastOutput = "";
    let hideLogUntilChange = false;

    function updateEmptyState() {
        emptyState.hidden = canvas.children.length > 0;
    }

    function updateTargetFields(block) {
        const isSsh = block.querySelector(".target-protocol").value === "ssh";
        block.querySelectorAll(".ssh-script-field").forEach(field => field.hidden = !isSsh);
        block.querySelector(".device-delay-field").hidden = block.querySelector(".target-mode").value === "parallel";
    }

    function bindBlock(block) {
        block.querySelector(".script-remove").addEventListener("click", () => {
            block.remove();
            updateEmptyState();
            saveDraft();
        });
        block.addEventListener("dragstart", event => {
            draggedBlock = block;
            block.classList.add("is-dragging");
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", "script-block");
        });
        block.addEventListener("dragend", () => {
            block.classList.remove("is-dragging");
            draggedBlock = null;
            saveDraft();
        });
        block.querySelectorAll("input, textarea, select").forEach(control => {
            control.addEventListener("input", saveDraft);
            control.addEventListener("change", saveDraft);
            control.addEventListener("pointerdown", () => block.draggable = false);
            control.addEventListener("pointerup", () => block.draggable = true);
            control.addEventListener("blur", () => block.draggable = true);
        });
        if (block.dataset.type === "target") {
            block.querySelector(".target-protocol").addEventListener("change", () => {
                const protocol = block.querySelector(".target-protocol").value;
                if (protocol === "ssh" && block.querySelector(".target-port").value === "23") {
                    block.querySelector(".target-port").value = "22";
                } else if (protocol === "telnet" && block.querySelector(".target-port").value === "22") {
                    block.querySelector(".target-port").value = "23";
                }
                updateTargetFields(block);
            });
            block.querySelector(".target-mode").addEventListener("change", () => updateTargetFields(block));
            updateTargetFields(block);
        }
    }

    function addBlock(type, values = null, focus = true) {
        const template = document.getElementById(`${type}BlockTemplate`);
        const block = template.content.firstElementChild.cloneNode(true);
        canvas.appendChild(block);
        bindBlock(block);
        if (values) applyValues(block, values);
        updateEmptyState();
        saveDraft();
        if (focus) block.querySelector("input, textarea")?.focus();
        return block;
    }

    function collectBlock(block) {
        if (block.dataset.type === "target") {
            return {
                type: "target",
                targets: block.querySelector(".target-list").value,
                protocol: block.querySelector(".target-protocol").value,
                port: block.querySelector(".target-port").value,
                mode: block.querySelector(".target-mode").value,
                device_delay: block.querySelector(".device-delay").value,
                username: block.querySelector(".target-username").value,
                password: block.querySelector(".target-password").value
            };
        }
        if (block.dataset.type === "command") {
            return {
                type: "command",
                value: block.querySelector(".command-value").value,
                is_hex: block.querySelector(".command-hex").checked,
                add_cr: block.querySelector(".command-cr").checked,
                add_lf: block.querySelector(".command-lf").checked
            };
        }
        return {type: "delay", duration: block.querySelector(".delay-duration").value};
    }

    function collectBlocks() {
        return Array.from(canvas.children).map(collectBlock);
    }

    function applyValues(block, values) {
        if (values.type === "target") {
            block.querySelector(".target-list").value = values.targets || "";
            block.querySelector(".target-protocol").value = values.protocol || "tcp";
            block.querySelector(".target-port").value = values.port || "23";
            block.querySelector(".target-mode").value = values.mode || "sequential";
            block.querySelector(".device-delay").value = values.device_delay || "0";
            block.querySelector(".target-username").value = values.username || "";
            block.querySelector(".target-password").value = values.password || "";
            updateTargetFields(block);
        } else if (values.type === "command") {
            block.querySelector(".command-value").value = values.value || "";
            block.querySelector(".command-hex").checked = !!values.is_hex;
            block.querySelector(".command-cr").checked = !!values.add_cr;
            block.querySelector(".command-lf").checked = values.add_lf !== false;
        } else {
            block.querySelector(".delay-duration").value = values.duration ?? "1";
        }
    }

    function saveDraft() {
        try {
            const safeBlocks = collectBlocks().map(block => (
                block.type === "target" ? {...block, password: ""} : block
            ));
            localStorage.setItem(storageKey, JSON.stringify(safeBlocks));
        } catch (_error) {
            // The canvas remains usable when browser storage is unavailable.
        }
    }

    function loadDraft() {
        try {
            const blocks = JSON.parse(localStorage.getItem(storageKey) || "null");
            if (Array.isArray(blocks) && blocks.length) {
                blocks.filter(block => ["target", "command", "delay"].includes(block.type))
                    .forEach(block => addBlock(block.type, block, false));
                return;
            }
        } catch (_error) {
            // Fall through to a useful starter canvas.
        }
        addBlock("target", null, false);
        addBlock("command", null, false);
    }

    function replaceCanvas(blocks) {
        canvas.innerHTML = "";
        (blocks || []).forEach(block => addBlock(block.type, block, false));
        updateEmptyState();
        saveDraft();
    }

    function setSaveStatus(message, success = false) {
        saveStatus.textContent = message;
        saveStatus.classList.toggle("success", success);
    }

    async function refreshSavedScripts(selectedName = "") {
        try {
            const response = await fetch(urls.savedUrl);
            const data = await response.json();
            savedSelect.innerHTML = '<option value="">Select a saved script...</option>';
            (data.scripts || []).forEach(item => {
                const option = document.createElement("option");
                option.value = item.name;
                option.textContent = `${item.name} (${item.block_count} blocks)`;
                savedSelect.appendChild(option);
            });
            savedSelect.value = selectedName;
            deleteButton.disabled = !savedSelect.value;
            loadButton.disabled = !savedSelect.value;
        } catch (error) {
            setSaveStatus(`Could not load saved scripts: ${error.message}`);
        }
    }

    async function loadSavedScript() {
        if (!savedSelect.value) return;
        const response = await fetch(urls.entryUrl.replace("__NAME__", encodeURIComponent(savedSelect.value)));
        const data = await response.json();
        if (!response.ok || !data.success) {
            alert(data.message || "Could not load script.");
            return;
        }
        replaceCanvas(data.script.blocks);
        setSaveStatus(`Loaded ${data.script.name}.`, true);
    }

    async function saveNamedScript(overwrite = false, existingName = "") {
        const name = existingName || prompt("Save script as:");
        if (!name) return;
        const response = await fetch(urls.saveUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name, blocks: collectBlocks(), overwrite})
        });
        const data = await response.json();
        if (data.message === "NAME_EXISTS") {
            if (confirm(`A script named "${name}" already exists. Overwrite it?`)) {
                await saveNamedScript(true, name);
            }
            return;
        }
        if (!response.ok || !data.success) {
            alert(data.message || "Could not save script.");
            return;
        }
        await refreshSavedScripts(name);
        setSaveStatus(`Saved ${name}. Passwords are not stored.`, true);
    }

    async function deleteSavedScript() {
        const name = savedSelect.value;
        if (!name || !confirm(`Delete the saved script "${name}"?`)) return;
        const response = await fetch(urls.deleteUrl.replace("__NAME__", encodeURIComponent(name)), {method: "DELETE"});
        const data = await response.json();
        if (!response.ok || !data.success) {
            alert(data.message || "Could not delete script.");
            return;
        }
        await refreshSavedScripts();
        setSaveStatus(`Deleted ${name}.`, true);
    }

    function blockAfterPointer(y) {
        return Array.from(canvas.querySelectorAll(".script-block:not(.is-dragging)"))
            .reduce((closest, block) => {
                const box = block.getBoundingClientRect();
                const offset = y - box.top - box.height / 2;
                return offset < 0 && offset > closest.offset ? {offset, block} : closest;
            }, {offset: Number.NEGATIVE_INFINITY, block: null}).block;
    }

    function renderStatus(data) {
        statusBox.textContent = data.status_text || (data.running ? "Running" : "Idle");
        runButton.disabled = !!data.running;
        pauseButton.disabled = !data.running;
        stopButton.disabled = !data.running;
        pauseButton.textContent = data.paused ? "Resume" : "Pause";
        canvas.classList.toggle("is-running", !!data.running);
        Array.from(canvas.children).forEach((block, index) => {
            block.classList.toggle("is-current", data.running && data.current_block === index);
        });
        const rendered = (data.output || []).map(item => `[${item.time}] ${item.message}`).join("\n");
        if (rendered !== lastOutput) {
            lastOutput = rendered;
            if (hideLogUntilChange) hideLogUntilChange = false;
            if (!hideLogUntilChange) {
                output.textContent = rendered || "No script run yet.";
                output.scrollTop = output.scrollHeight;
            }
        }
    }

    async function post(url, body) {
        try {
            const response = await fetch(url, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body || {})
            });
            const data = await response.json();
            renderStatus(data);
            if (!response.ok || !data.success) alert(data.message || "The action could not be completed.");
        } catch (error) {
            alert(`Request failed: ${error.message}`);
        }
    }

    async function refreshStatus() {
        try {
            const response = await fetch(urls.statusUrl);
            renderStatus(await response.json());
        } catch (_error) {
            statusBox.textContent = "Status unavailable";
        }
    }

    document.getElementById("addTargetBlock").addEventListener("click", () => addBlock("target"));
    document.getElementById("addCommandBlock").addEventListener("click", () => addBlock("command"));
    document.getElementById("addDelayBlock").addEventListener("click", () => addBlock("delay"));
    document.getElementById("saveScript").addEventListener("click", () => saveNamedScript());
    loadButton.addEventListener("click", loadSavedScript);
    deleteButton.addEventListener("click", deleteSavedScript);
    savedSelect.addEventListener("change", () => {
        loadButton.disabled = !savedSelect.value;
        deleteButton.disabled = !savedSelect.value;
    });
    runButton.addEventListener("click", () => post(urls.startUrl, {blocks: collectBlocks()}));
    pauseButton.addEventListener("click", () => post(urls.pauseUrl, {paused: pauseButton.textContent === "Pause"}));
    stopButton.addEventListener("click", () => post(urls.stopUrl));
    document.getElementById("clearScriptLog").addEventListener("click", () => {
        output.textContent = "Log view cleared.";
        hideLogUntilChange = true;
    });
    canvas.addEventListener("dragover", event => {
        if (!draggedBlock) return;
        event.preventDefault();
        const nextBlock = blockAfterPointer(event.clientY);
        if (nextBlock) canvas.insertBefore(draggedBlock, nextBlock);
        else canvas.appendChild(draggedBlock);
    });
    canvas.addEventListener("drop", event => {
        if (draggedBlock) event.preventDefault();
    });

    loadDraft();
    refreshSavedScripts();
    refreshStatus();
    setInterval(refreshStatus, 500);
})();
