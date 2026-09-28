(function () {
    const urls = document.currentScript.dataset;
    const el = id => document.getElementById(id);
    const text = (id, value) => { el(id).textContent = value; };
    const escapeHtml = value => String(value ?? "-").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
    const stamp = value => value == null ? "-" : new Date(value * 1000).toLocaleTimeString();
    const number = (value, digits = 0) => Number(value || 0).toFixed(digits);
    const cells = values => values.map(v => `<td>${escapeHtml(v)}</td>`).join("");
    let latest = null, running = false, busy = false, leaving = false;
    const opened = new Set();
    const rows = new Map();

    function rtpText(r) {
        return `SSRC ${r.ssrc}, PT ${r.payload_type}: ${r.packets} packets, estimated missing ${r.estimated_missing_packets}, duplicates ${r.duplicate_packets}, out of order ${r.out_of_order_packets}, largest sequence gap ${r.largest_packet_gap}, largest arrival gap ${number(r.largest_arrival_gap_ms, 2)} ms, estimated jitter ${number(r.estimated_network_jitter_ms, 3)} ms; latest sequence ${r.sequence_number}, RTP timestamp ${r.rtp_timestamp}, discontinuities ${r.discontinuities}; ${stamp(r.first_seen)} - ${stamp(r.last_seen)}`;
    }

    function renderStreams(streams) {
        const filter = el("streamFilter").value.toLowerCase();
        const sort = el("streamSort").value;
        const descending = ["mbps", "packets", "packets_per_second"].includes(sort);
        const ordered = [...streams].sort((a, b) => {
            const x = a[sort], y = b[sort];
            const comparison = typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""), undefined, {numeric:true});
            return (descending ? -comparison : comparison) || a.first_seen - b.first_seen || a.id.localeCompare(b.id);
        });
        const body = el("multicastStreams");
        const retained = new Set(streams.map(s => s.id));
        for (const [id, row] of rows) {
            if (!retained.has(id)) { row.remove(); rows.delete(id); opened.delete(id); }
        }
        ordered.forEach((s, index) => {
            let row = rows.get(s.id);
            if (!row) {
                row = document.createElement("tr");
                row.dataset.streamId = s.id;
                row.innerHTML = `<td><details><summary></summary><div class="analyzer-detail"></div></details></td>` + "<td></td>".repeat(11);
                row.querySelector("details").addEventListener("toggle", event => {
                    if (event.target.open) opened.add(s.id); else opened.delete(s.id);
                });
                rows.set(s.id, row);
            }
            row.hidden = ![s.source,s.group,s.udp_source_port,s.udp_destination_port,s.possible_protocol,s.dscp,s.dscp_class,s.status].join(" ").toLowerCase().includes(filter);
            row.querySelector("summary").textContent = s.source;
            row.querySelector("details").open = opened.has(s.id);
            row.querySelector(".analyzer-detail").textContent = `IP protocol ${s.ip_protocol}; ECN ${s.ecn_values.join(", ")}; TTL values ${s.ttl_values.join(", ")}; first ${stamp(s.first_seen)}, last ${stamp(s.last_seen)}. DSCP history (latest 64 changes): ${s.dscp_history.map(h => `${stamp(h.timestamp)}: ${h.value} ${h.class}`).join("; ")}. ${s.rtp.map(rtpText).join(" | ")}`;
            const values = [s.group, `${s.udp_source_port ?? "-"} → ${s.udp_destination_port ?? "-"}`, s.possible_protocol, number(s.mbps,3), number(s.packets_per_second,1), s.packets, number(s.average_packet_size,1), `${s.ttl}${s.mixed_ttl ? " (mixed)" : ""}`, `${s.dscp} ${s.dscp_class}${s.mixed_dscp ? " (mixed)" : ""}`, stamp(s.last_seen), s.status];
            values.forEach((v, index) => { row.children[index + 1].textContent = v; });
            // Preserve nodes, focus and expanded details when the order has not changed.
            if (body.children[index] !== row) body.insertBefore(row, body.children[index] || null);
        });
        let empty = body.querySelector(".analyzer-empty");
        if (!empty) { empty = document.createElement("tr"); empty.className = "analyzer-empty"; empty.innerHTML = '<td colspan="12">No matching multicast streams observed.</td>'; body.appendChild(empty); }
        empty.hidden = [...rows.values()].some(row => !row.hidden);
    }

    const groupRows = new Map();
    function renderGroups(streams) {
        const groups = new Map();
        streams.forEach(stream => {
            // Validated named services can span their standard ports (PTP 319/320).
            const named = ["PTP", "mDNS", "LLMNR", "SSDP"].includes(stream.possible_protocol);
            const key = JSON.stringify([stream.group, stream.ip_protocol, named ? stream.possible_protocol : stream.udp_destination_port]);
            if (!groups.has(key)) groups.set(key, {key, label: named ? stream.group : `${stream.group}:${stream.udp_destination_port ?? "-"}`, streams:[], mbps:0, active:false, sources:new Set(), protocols:new Set()});
            const group = groups.get(key);
            group.streams.push(stream); group.mbps += stream.mbps;
            group.active = group.active || stream.active === true || stream.status !== "Inactive";
            group.sources.add(stream.source); group.protocols.add(stream.possible_protocol);
        });
        const body = el("groupedStreams");
        for (const [key, row] of groupRows) {
            if (!groups.has(key)) { row.remove(); groupRows.delete(key); }
        }
        const ordered = [...groups.values()].sort((a,b) => Number(b.active)-Number(a.active) || b.mbps-a.mbps || a.label.localeCompare(b.label));
        ordered.forEach((group, index) => {
            let row = groupRows.get(group.key);
            if (!row) {
                row = document.createElement("details");
                row.className = "health-group";
                row.innerHTML = '<summary></summary><div class="table-wrap group-stream-details"></div>';
                row.addEventListener("toggle", () => { if (row.open) renderGroupDetails(row); });
                groupRows.set(group.key, row);
            }
            row.groupData = group;
            row.hidden = !group.active && !el("showInactiveGroups").checked;
            row.querySelector("summary").textContent = `${group.label} | ${[...group.protocols].join(", ")} | ${group.sources.size} sources | ${number(group.mbps,3)} Mbps${group.active ? "" : " | Inactive"}`;
            if (row.open) renderGroupDetails(row);
            if (body.children[index] !== row) body.insertBefore(row, body.children[index] || null);
        });
        text("groupCount", `${ordered.filter(g => g.active).length} active groups; ${ordered.filter(g => !g.active).length} inactive. Highest bandwidth first.`);
    }
    function renderGroupDetails(row) {
        const markup = '<table class="result-table analyzer-table"><thead><tr>' +
            ["Source", "UDP ports", "Protocol", "Packets/s", "Mbps", "Packets", "Avg bytes", "TTL", "DSCP", "Last seen", "Status", "More details"].map(v => `<th>${v}</th>`).join("") +
            '</tr></thead><tbody>' + row.groupData.streams.map(s => `<tr>${cells([s.source, `${s.udp_source_port ?? "-"} to ${s.udp_destination_port ?? "-"}`, s.possible_protocol, number(s.packets_per_second,1), number(s.mbps,3), s.packets, number(s.average_packet_size,1), s.ttl_values.join(", "), `${s.dscp} ${s.dscp_class}`, stamp(s.last_seen), s.status, `First seen ${stamp(s.first_seen)}; IP protocol ${s.ip_protocol}; ECN ${s.ecn_values.join(", ")}; DSCP history: ${s.dscp_history.map(h => `${stamp(h.timestamp)}: ${h.value} ${h.class}`).join("; ")}; ${s.rtp.map(rtpText).join(" | ")}`])}</tr>`).join("") + '</tbody></table>';
        const target = row.querySelector(".group-stream-details");
        if (target.dataset.markup !== markup) { target.innerHTML = markup; target.dataset.markup = markup; }
    }
    function renderHealth(data) {
        const ready = data.elapsed_seconds > 0 || Boolean(data.error);
        text("networkHealth", ready ? `Network Health: ${data.health_status}` : "Network Health: Ready to check");
        el("networkHealth").dataset.health = ready ? data.health_status : "pending";
        text("healthMessage", data.health_message || "Start a check to collect evidence.");
        text("healthConfidence", data.health_confidence === "sufficient" ? "Observation window complete. Results apply to traffic visible at this interface." : "Assessment incomplete. Observe for at least 130 seconds; no visible traffic cannot establish network health.");
        const checks = data.health_checks || {};
        el("healthChecks").innerHTML = Object.entries(checks).map(([area, check]) => `<div><span>${escapeHtml(area.toUpperCase())}</span><strong>${escapeHtml(check.summary)}</strong><span>${escapeHtml(check.detail)}</span></div>`).join("");
        const findings = data.actionable_findings || [];
        el("healthFindings").innerHTML = findings.length ? findings.map(f => `<article class="health-finding" data-health="${escapeHtml(f.level)}"><strong>${escapeHtml(f.level)}: ${escapeHtml(f.title)}</strong><p>${escapeHtml(f.detail)}</p></article>`).join("") : `<p>${ready ? "No problems detected in observed traffic." : "Start a check to collect evidence."}</p>`;
        for (const [area, id] of [["igmp", "igmpHealthSummary"], ["ptp", "ptpHealthSummary"], ["rtp", "rtpHealthSummary"]]) {
            const check = checks[area];
            text(id, check ? `${area.toUpperCase()} Health: ${check.status.replace(/_/g, " ")} - ${check.summary}. ${check.detail}` : "Waiting for observation.");
        }
    }

    function render(data) {
        latest = data;
        renderHealth(data);
        renderGroups(data.streams);
        running = Boolean(data.running);
        text("multicastStatus", data.message || "Idle");
        el("startMulticast").disabled = running || busy;
        el("stopMulticast").disabled = !running || busy;
        el("multicastNic").disabled = running || busy;
        el("downloadMulticastReport").hidden = running || !data.interface;
        if (running) el("multicastNic").value = data.interface;
        text("summaryCapture", running ? "Capturing" : data.error ? "Error" : "Stopped / idle");
        text("summaryInterface", data.interface || "-"); text("summaryIp", data.ip || "-");
        text("summaryStreams", data.active_streams || 0);
        text("summaryQuerier", data.querier_detected ? "Observed recently" : "Not observed recently");
        text("summaryPtp", `${data.ptp_domains.length} / ${data.ptp_sources.filter(c => c.active).length}`);
        text("summaryWarnings", data.warning_count || 0);
        text("summaryElapsed", `${number(data.elapsed_seconds,1)} seconds ${running ? "elapsed" : "captured (snapshot frozen)"}`);
        text("multicastTotalRate", `${number(data.total_mbps,3)} Mbps`);
        const warningMarkup = data.warnings.length ? data.warnings.map(w => `<div class="diagnostic-warning ${escapeHtml(w.severity)}">${escapeHtml(w.severity.toUpperCase())}: ${escapeHtml(w.message)}${w.sources ? `<details><summary>Sources (${w.sources.length})</summary>${w.sources.map(escapeHtml).join(", ")}</details>` : ""}</div>`).join("") : '<p>No diagnostics so far. Observe for at least 130 seconds to evaluate querier visibility.</p>';
        // Retain expanded source lists while polling unchanged diagnostics.
        if (el("multicastWarnings").dataset.markup !== warningMarkup) {
            el("multicastWarnings").innerHTML = warningMarkup;
            el("multicastWarnings").dataset.markup = warningMarkup;
        }
        const counts = data.igmp_counts;
        text("summaryVersion", data.igmp_versions.join(", ") || "Not observed");
        text("summaryIgmpCounts", `${counts.query || 0} / ${counts.report || 0} / ${counts.leave || 0}`);
        text("queryKinds", `General queries: ${counts.general_query || 0}; group-specific queries: ${counts.group_query || 0}; malformed: ${counts.invalid || 0}`);
        el("querierDetails").innerHTML = data.queriers.map(q => `<p>${escapeHtml(q.ip)}: last query ${q.last_query_seconds} seconds ${running ? "ago" : "before stop"}; estimated interval ${q.query_interval_seconds ?? "measuring"} seconds</p>`).join("") || "No query sources observed.";
        text("joinedGroups", data.membership_available ? data.joined_groups.join(", ") || "None" : "Windows membership data unavailable");
        text("floodingObservations", data.warnings.filter(w => ["snooping","unjoined_traffic","unjoined_observed","membership_unknown"].includes(w.code)).map(w => w.message).join(" ") || "No current flooding observations.");
        el("igmpEvents").innerHTML = [...data.igmp_events].reverse().map(e => `<tr>${cells([stamp(e.timestamp),e.source,e.version,e.event_type,e.groups.join(", ") || "All groups / none"])}</tr>`).join("") || '<tr><td colspan="5">No IGMP events observed.</td></tr>';
        text("dscpDistribution", data.dscp_distribution.map(d => `${d.value} (${d.class}): ${d.packets} packets`).join(" · ") || "No multicast markings observed.");
        text("ptpDomains", `Recent domains: ${data.ptp_domains.join(", ") || "None"}. Announce sources are not definitive grandmaster identifications.`);
        el("ptpSources").innerHTML = data.ptp_sources.map(c => `<tr>${cells([`${c.clock_identity} / ${c.source_port_number}`,`${c.domain} / v${c.version}`,c.source,c.announce_active ? "Observed recently" : c.last_announce != null ? "No longer observed" : "Not observed",Object.entries(c.message_counts).map(([k,v]) => `${k}: ${v}`).join(", "),`${c.sequence_id} / log2 ${c.log_message_interval ?? "unspecified"}`,c.dscp_values.join(", "),`${stamp(c.first_seen)} / ${stamp(c.last_seen)}`])}</tr>`).join("") || '<tr><td colspan="8">No PTP v2 over UDP observed.</td></tr>';
        el("rtpSummary").innerHTML = data.streams.filter(s => s.rtp.length).map(s => `<p><strong>${escapeHtml(s.source)} → ${escapeHtml(s.group)}:${s.udp_destination_port}</strong> (${escapeHtml(s.status)})<br>${s.rtp.map(r => escapeHtml(rtpText(r))).join("<br>")}</p>`).join("") || "No repeated RTP candidates observed.";
        renderStreams(data.streams);
    }

    async function command(url, options) {
        busy = true;
        el("startMulticast").disabled = el("stopMulticast").disabled = true;
        try {
            const response = await fetch(url, options);
            const data = await response.json();
            render(data);
            if (!response.ok || data.success === false) alert(data.message || "Capture request failed.");
        } catch (error) { text("multicastStatus", `Capture request failed: ${error.message}`); }
        finally { busy = false; if (latest) { el("startMulticast").disabled = running; el("stopMulticast").disabled = !running; el("multicastNic").disabled = running; } }
    }
    el("startMulticast").addEventListener("click", () => command(urls.startUrl, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({interface:el("multicastNic").value})}));
    el("stopMulticast").addEventListener("click", () => command(urls.stopUrl, {method:"POST"}));
    el("showInactiveGroups").addEventListener("change", () => { if (latest) renderGroups(latest.streams); });
    el("streamFilter").addEventListener("input", () => { if (latest) renderStreams(latest.streams); });
    el("streamSort").addEventListener("change", () => { if (latest) renderStreams(latest.streams); });
    el("downloadMulticastReport").addEventListener("click", async () => {
        el("downloadMulticastReport").disabled = true;
        text("multicastExportStatus", "Saving report...");
        try {
            const response = await fetch(urls.exportUrl, {method:"POST"});
            const data = await response.json();
            text("multicastExportStatus", data.cancelled ? "" : data.success ? `Saved ${data.filename}.` : data.message || "Could not save report.");
        } catch (error) { text("multicastExportStatus", error.message); }
        finally { el("downloadMulticastReport").disabled = false; }
    });
    let timer;
    async function refresh() {
        if (leaving) return;
        try {
            if (!busy) {
                const response = await fetch(urls.statusUrl);
                if (!response.ok) throw new Error("Status request failed");
                const data = await response.json();
                if (!busy && !leaving) render(data);
            }
        } catch (_error) { text("multicastStatus", "Could not refresh health-check status."); text("healthConfidence", "Status unavailable. Displayed results may be stale; reconnect before relying on this assessment."); }
        if (!leaving) timer = setTimeout(refresh, 1000);
    }
    window.addEventListener("pagehide", () => {
        leaving = true; clearTimeout(timer);
        if (running || busy) {
            if (!navigator.sendBeacon(urls.stopUrl)) fetch(urls.stopUrl, {method:"POST",keepalive:true}).catch(() => {});
        }
    });
    window.addEventListener("pageshow", event => { if (event.persisted) { leaving = false; refresh(); } });
    refresh();
})();
