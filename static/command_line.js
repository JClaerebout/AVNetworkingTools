(function () {
    const runUrl = document.currentScript.dataset.runUrl;
    const form = document.getElementById("commandForm");
    const input = document.getElementById("commandInput");
    const workingDirectory = document.getElementById("commandWorkingDirectory");
    const output = document.getElementById("commandOutput");
    const runButton = document.getElementById("runCommand");
    const clearButton = document.getElementById("clearCommandOutput");
    const history = [];
    let historyIndex = 0;

    if (!form) return;

    function appendOutput(text) {
        if (output.textContent === "Ready.") output.textContent = "";
        output.textContent += text;
        output.scrollTop = output.scrollHeight;
    }

    async function runCommand(event) {
        event.preventDefault();
        const command = input.value.trim();
        if (!command || runButton.disabled) return;

        history.push(command);
        historyIndex = history.length;
        input.value = "";
        runButton.disabled = true;
        input.disabled = true;
        appendOutput(`\n${workingDirectory.value}> ${command}\n`);

        try {
            const response = await fetch(runUrl, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    command,
                    working_directory: workingDirectory.value
                })
            });
            const data = await response.json();
            if (data.working_directory) workingDirectory.value = data.working_directory;
            if (data.output) appendOutput(data.output);
            appendOutput(`[${data.message} Exit code: ${data.exit_code ?? "none"}]\n`);
        } catch (error) {
            appendOutput(`[Request failed: ${error.message}]\n`);
        } finally {
            runButton.disabled = false;
            input.disabled = false;
            input.focus();
        }
    }

    input.addEventListener("keydown", event => {
        if (event.key === "ArrowUp" && history.length) {
            event.preventDefault();
            historyIndex = Math.max(0, historyIndex - 1);
            input.value = history[historyIndex];
        } else if (event.key === "ArrowDown" && history.length) {
            event.preventDefault();
            historyIndex = Math.min(history.length, historyIndex + 1);
            input.value = historyIndex < history.length ? history[historyIndex] : "";
        }
    });

    clearButton.addEventListener("click", () => {
        output.textContent = "Ready.";
        input.focus();
    });
    form.addEventListener("submit", runCommand);
    input.focus();
})();
