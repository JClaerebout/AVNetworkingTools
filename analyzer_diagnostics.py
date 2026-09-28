"""Diagnostic evaluation on JSON-safe snapshots; optional profile expectations are explicit."""


def evaluate(snapshot, now, profile=None):
    diagnostics = []

    def add(code, message, severity="warning", stream_id=None):
        diagnostics.append(dict(code=code, message=message, severity=severity, stream_id=stream_id))

    best_effort = {}
    for stream in snapshot["streams"]:
        label = f'{stream["source"]} → {stream["group"]}:{stream["udp_destination_port"] or "-"}'
        if stream["mixed_dscp"] and stream["possible_protocol"] != "PTP":
            add("mixed_dscp", f'{label}: mixed DSCP values; {stream["dscp_changes"]} changes observed during capture.', stream_id=stream["id"])
        expected = (profile or {}).get("expected_dscp", {}).get(stream["possible_protocol"])
        if expected is not None and any(v != expected for v in stream["dscp_values"]):
            add("profile_dscp", f"{label}: selected profile expects DSCP {expected}.", stream_id=stream["id"])
        elif 0 in stream["dscp_values"]:
            key = (stream["group"], stream["ip_protocol"], stream["udp_destination_port"])
            best_effort.setdefault(key, set()).add(stream["source"])
    for (group, protocol, port), sources in sorted(best_effort.items(), key=lambda item: str(item[0])):
        add("best_effort", f"{group}:{port or '-'} (IP protocol {protocol}): {len(sources)} sources using CS0 (Best Effort); no universal AV marking is assumed.", "information")
        diagnostics[-1]["sources"] = sorted(sources)

    active = [c for c in snapshot["ptp_sources"] if c["active"]]
    if len({c["domain"] for c in active}) > 1:
        add("ptp_domains", "Multiple recent PTP domains observed; confirm the intended timing domains.")
    domains = {}
    for clock in snapshot["ptp_sources"]:
        if clock["announce_active"]:
            domains.setdefault(clock["domain"], set()).add((clock["clock_identity"], clock["source_port_number"]))
        elif clock["last_announce"] is not None:
            add("ptp_announce_missing", f'Previously active announce source {clock["clock_identity"]} in domain {clock["domain"]} is no longer observed; this does not establish grandmaster loss.')
    for domain, sources in domains.items():
        if len(sources) > 1:
            add("ptp_announce_sources", f"Multiple announce sources in PTP domain {domain}; election or multiple clocks may be present.")
    for clock in active:
        for message_type, values in clock["dscp_by_message_type"].items():
            if len(values) > 1:
                add("ptp_dscp", f'{clock["source"]}, clock {clock["clock_identity"]}/{clock["source_port_number"]}, domain {clock["domain"]}, {message_type}: DSCP changed between {values}.')
    candidates = snapshot.get("unrecognized_ptp_candidates", 0)
    if candidates:
        add("possible_ptp", f"{candidates} UDP 319/320 packets could not be validated as supported PTP v2. Possible PTP or other UDP traffic; not counted as malformed PTP.", "information")
    for protocol, count in snapshot["malformed_packets"].items():
        if count:
            add("malformed_" + protocol, f"{count} malformed, truncated or unsupported {protocol.upper()} packets observed.")
    memberships = {}
    for event in snapshot["igmp_events"]:
        # Repeated reports are membership assertions, not repeated joins. V3
        # source-filter reports do not establish whole-group leave transitions.
        if not 0 <= now - event["timestamp"] <= 10 or event["version"] not in ("v1", "v2") or event["event_type"] not in ("report", "leave"):
            continue
        for group in event["groups"]:
            state = memberships.setdefault((event["source"], group), {"last": None, "transitions": 0})
            if state["last"] is not None and state["last"] != event["event_type"]:
                state["transitions"] += 1
            state["last"] = event["event_type"]
    for (source, group), state in memberships.items():
        if state["transitions"] >= 6:
            add("igmp_churn", f"{source} / {group}: {state['transitions']} report/leave transitions in 10 seconds; possible membership flapping.")
            diagnostics[-1].update(source=source, group=group, transitions=state["transitions"], window_seconds=10)
    if any(snapshot["evicted_records"].values()):
        add("capacity", "Analyzer record limits reached; oldest records were evicted. Capture totals are retained.", "information")
    return diagnostics
