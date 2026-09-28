"""Conservative commissioning assessment, independent of diagnostic wording.

Raw warnings remain available unchanged. No link speed or timing profile is
known, so bandwidth and PTP marking observations cannot prove a network fault.
"""
import ipaddress


HIGH_GROUP_MBPS = 5.0
HIGH_TOTAL_MBPS = 10.0
UNJOINED_SUBSTANTIAL_MBPS = 100.0
UNJOINED_SUSTAINED_SECONDS = 30


def sustained_bandwidth(buckets, now):
    """Minimum over complete seconds; absent seconds count as zero."""
    end = int(now)
    values = {b[0]: b[2] * 8 / 1_000_000 for b in buckets}
    return min(values.get(second, 0) for second in range(end - UNJOINED_SUSTAINED_SECONDS, end))


def apply_health(status):
    """Project one health decision into diagnostic severity and group metadata."""
    health = assess(status)
    findings = {f["code"]: f for f in health["actionable_findings"]}
    diagnostics = []
    for original in status.get("warnings", []):
        row = dict(original)
        finding = findings.get(row.get("code"))
        row["severity"] = ("error" if finding["level"] == "PROBLEM" else "warning") if finding else "information"
        diagnostics.append(row)
    for code, finding in findings.items():
        if not any(w.get("code") == code for w in diagnostics):
            diagnostics.append(dict(code=code, severity="error" if finding["level"] == "PROBLEM" else "warning",
                                    message=finding["title"] + " " + finding["detail"]))
    affected = set(next((f.get("groups", []) for f in findings.values() if f["code"] == "possible_flooding"), []))
    for group in status.get("groups", []):
        group["suspected_flood"] = group["address"] in affected
        group["assessment"] = "Possible flooding" if group["suspected_flood"] else "Observed"
    review_ids = {w.get("stream_id") for w in diagnostics if w["severity"] != "information"}
    for stream in status.get("streams", []):
        stream["status"] = "Inactive" if not stream.get("active") else "Review" if stream["group"] in affected or stream.get("id") in review_ids - {None} else "Observed"
    diagnostics.sort(key=lambda w: {"error": 0, "warning": 1, "information": 2}[w["severity"]])
    status.update(health)
    status["warnings"] = diagnostics
    status["warning_count"] = sum(w["severity"] != "information" for w in diagnostics)
    status["diagnostic_information"] = [w for w in diagnostics if w["severity"] == "information"]
    return status


def assess(status):
    findings = []
    consumed = set()
    warnings = status.get("warnings", [])
    codes = {w.get("code") for w in warnings}
    elapsed = status.get("elapsed_seconds", 0)
    sufficient = elapsed >= status.get("no_querier_warning_after_seconds", 130)
    streams = status.get("streams", [])
    counts = status.get("igmp_counts", {})

    def add(code, area, title, detail, level="ATTENTION"):
        findings.append(dict(code=code, area=area, title=title, detail=detail, level=level))
        consumed.add(code)

    if status.get("error"):
        add("capture", "capture", "The health check could not complete.",
            "Resolve the capture error in Technical Details and run the check again.")
    substantial = [g["address"] for g in status.get("groups", [])
                   if sufficient and g.get("membership_known") and not g.get("joined")
                   and not g["address"].startswith("224.0.0.")
                   and g.get("sustained_min_mbps", 0) >= UNJOINED_SUBSTANTIAL_MBPS]
    if substantial:
        add("possible_flooding", "multicast", "Possible multicast flooding.",
            f"{len(substantial)} unjoined group(s) delivered at least {UNJOINED_SUBSTANTIAL_MBPS:g} Mbps in every complete second of the last {UNJOINED_SUSTAINED_SECONDS} seconds. Check whether this traffic is expected at the port; passive capture cannot establish switch configuration.")
        findings[-1]["groups"] = substantial
    if status.get("total_mbps", 0) >= HIGH_TOTAL_MBPS or any(g.get("mbps", 0) >= HIGH_GROUP_MBPS for g in status.get("groups", [])):
        add("high_traffic", "multicast", "Multicast load warrants review.",
            f"At least {HIGH_TOTAL_MBPS:g} Mbps total or {HIGH_GROUP_MBPS:g} Mbps in one group was observed in the current five-second window. Compare with link capacity and expected AV traffic; this alone does not establish a storm.")

    # Reports alone do not imply flapping. Expect query visibility only with
    # observed membership activity and non-link-local multicast traffic.
    managed_groups = any(ipaddress.ip_address(s["group"]) not in ipaddress.ip_network("224.0.0.0/24") for s in streams)
    expected = managed_groups and bool(counts.get("report") or counts.get("leave"))
    if sufficient and expected and not status.get("querier_detected") and not status.get("error"):
        add("no_querier", "igmp", "No IGMP querier observed after sufficient observation.",
            "Membership activity and non-link-local multicast were observed, but no recent query was visible after at least 130 seconds. Check the querier and capture-port visibility.", "PROBLEM")
    for code, area, title, action in (
        ("igmp_churn", "igmp", "Repeated membership changes need investigation.", "Check the affected endpoint and group for reconnects or membership instability."),
        ("mixed_dscp", "qos", "A stream changed its DSCP marking.", "Check whether the marking changes match the intended traffic policy."),
        ("ptp_dscp", "ptp", "PTP marking changed within one source and message type.", "Check this clock's configured QoS policy; packet markings do not prove a timing failure."),
        ("ptp_announce_missing", "ptp", "A previously observed PTP announce source disappeared.", "Check whether the clock stopped or an expected election occurred; this does not establish grandmaster loss."),
        ("profile_dscp", "qos", "Traffic differs from the selected DSCP policy.", "Check the explicit profile expectation against the endpoint configuration."),
    ):
        matches = [w for w in warnings if w.get("code") == code]
        if matches:
            severe = code == "igmp_churn" and any(w.get("transitions", 0) >= 20 for w in matches)
            add(code, area, title, f"{len(matches)} observation(s). {action} Open Technical Details for affected sources.", "PROBLEM" if severe else "ATTENTION")

    rtp_sources = [(s, r) for s in streams for r in s.get("rtp", [])]
    significant = [(s, r) for s, r in rtp_sources if r["packets"] >= 100 and r["estimated_missing_packets"] >= 20
                   and r["estimated_missing_packets"] / (r["packets"] + r["estimated_missing_packets"]) >= .05]
    if significant:
        add("rtp_loss", "rtp", "Significant RTP sequence gaps were observed.",
            f"{len(significant)} RTP source(s) have at least 100 captured packets, 20 estimated missing packets and 5% estimated loss. Check delivery and repeat the capture; capture loss or offloading can contribute.", "PROBLEM")
    elif any(r.get("estimated_missing_packets", 0) or r.get("discontinuities", 0) for _, r in rtp_sources):
        add("rtp_sequence", "rtp", "RTP sequence gaps or resets need review.",
            "Inspect RTP details and repeat the capture to distinguish endpoint resets, capture loss and delivery problems.")

    level = "PROBLEM" if any(f["level"] == "PROBLEM" for f in findings) else "ATTENTION" if findings else "HEALTHY"
    confidence = "incomplete" if status.get("error") or not elapsed or not status.get("packets") else "sufficient" if sufficient else "observing"
    message = ("Investigate the findings below." if findings else
               "No significant AV network problems detected in visible traffic." if confidence == "sufficient" else
               "No problems detected so far. The assessment is not yet conclusive.")
    checks = {}

    def check(area, summary, detail):
        relevant = [f for f in findings if f["area"] == area]
        state = "PROBLEM" if any(f["level"] == "PROBLEM" for f in relevant) else "ATTENTION" if relevant else "HEALTHY"
        checks[area] = dict(status=state, summary=summary if not relevant else "Needs investigation", detail=detail)

    recent = [q for q in status.get("queriers", []) if q["last_query_seconds"] < 130]
    query_detail = "; ".join(f'{q["ip"]} (~{q["query_interval_seconds"]} s)' if q.get("query_interval_seconds") is not None else q["ip"] for q in recent)
    check("igmp", "Querier detected" if recent else "Waiting for sufficient observation" if not sufficient else "Querier not observed",
          (query_detail or ("Query visibility expected for observed membership activity." if expected else "No evidence that this traffic requires an IGMP querier.")) +
          f' | Versions: {", ".join(status.get("igmp_versions", [])) or "not observed"}; leaves: {counts.get("leave", 0)}')
    check("multicast", "Normal observed load" if status.get("packets") else "No multicast observed", f'{status.get("total_mbps", 0):.3f} Mbps total')
    clocks = [c for c in status.get("ptp_sources", []) if c["active"]]
    check("ptp", "Detected; no obvious timing issue" if clocks else "No recent supported PTP observed",
          f'Domains: {", ".join(map(str, status.get("ptp_domains", []))) or "none"}; {len(clocks)} sources; announce seen: {"yes" if any(c["announce_active"] for c in clocks) else "no"}. DSCP stability: {"review" if "ptp_dscp" in codes else "no changes detected"}.')
    check("rtp", "No significant sequence issue detected" if rtp_sources else "No RTP streams detected", f"{len(rtp_sources)} repeated RTP source(s) observed; sequence-based estimates.")
    # Absence of protocol evidence is not a positive protocol-health result.
    for area, observed in (("igmp", bool(recent)), ("multicast", bool(status.get("packets"))),
                           ("ptp", bool(clocks)), ("rtp", bool(rtp_sources))):
        if not observed and checks[area]["status"] == "HEALTHY":
            checks[area]["status"] = "OBSERVING" if not sufficient else "NOT_OBSERVED"
    return dict(health_status=level, health_message=message, health_confidence=confidence,
                health_checks=checks, actionable_findings=findings,
                diagnostic_information=[w for w in warnings if w.get("code") not in consumed])
