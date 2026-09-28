(function () {
    function createLogView({output, search, count, latest, separator = "\n", follow = () => true}) {
        let sourceText = null;
        let sourceItems = [];
        let emptyText = "";
        let cleared = false;

        function update(resetScroll = false) {
            if (cleared) return;
            const query = search?.value.trim().toLowerCase() || "";
            const matches = query
                ? sourceItems.filter(item => item.toLowerCase().includes(query))
                : sourceItems;
            const nextText = query
                ? (matches.length ? matches.join(separator) : "No matching log entries.")
                : (sourceText || emptyText);
            if (count) count.textContent = query ? `${matches.length} match${matches.length === 1 ? "" : "es"}` : "";
            const wasAtBottom = output.scrollHeight - output.scrollTop - output.clientHeight <= 24;
            if (nextText !== output.textContent) {
                const previousTop = output.scrollTop;
                output.textContent = nextText;
                if (resetScroll) output.scrollTop = 0;
                else if (wasAtBottom && follow()) output.scrollTop = output.scrollHeight;
                else output.scrollTop = previousTop;
            } else if (resetScroll) {
                output.scrollTop = 0;
            }
        }

        search?.addEventListener("input", () => {
            cleared = false;
            update(true);
        });
        latest.addEventListener("click", () => {
            cleared = false;
            if (search) search.value = "";
            update();
            output.scrollTop = output.scrollHeight;
        });

        return {
            render(lines, placeholder) {
                const nextText = lines.join(separator);
                if (nextText === sourceText && placeholder === emptyText) return;
                sourceText = nextText;
                sourceItems = lines;
                emptyText = placeholder;
                cleared = false;
                update();
            },
            clearView() {
                cleared = true;
                output.textContent = "Log view cleared.";
                output.scrollTop = 0;
                if (count) count.textContent = "";
            }
        };
    }

    window.AVLogView = {createLogView};
})();
