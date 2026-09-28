"""Health decisions use evidence/codes, never diagnostic prose or raw severity."""
import copy
import json
import unittest

from analyzer_health import assess, apply_health, sustained_bandwidth
from routes import _multicast_report_content


def sample(**changes):
    value = dict(elapsed_seconds=136, packets=22949, total_mbps=.165,
                 no_querier_warning_after_seconds=130, streams=[], warnings=[],
                 querier_detected=True, queriers=[dict(ip="192.168.1.1", last_query_seconds=16,
                 query_interval_seconds=30)], igmp_versions=["v2"],
                 igmp_counts=dict(query=5, report=1249, leave=0), ptp_sources=[], ptp_domains=[])
    value.update(changes)
    return value


def warning(code, **extra):
    return dict(code=code, severity="warning", message="Raw diagnostic only", **extra)


class HealthTests(unittest.TestCase):
    def test_diagnostic_information_does_not_change_health(self):
        data = sample(warnings=[warning(c) for c in ("best_effort", "possible_ptp", "ptp_domains",
                      "ptp_announce_sources", "igmp_compatibility", "malformed_ptp", "capacity")])
        result = assess(data)
        self.assertEqual(result["health_status"], "HEALTHY")
        self.assertEqual(result["actionable_findings"], [])
        self.assertEqual(result["diagnostic_information"], data["warnings"])

    def test_unjoined_observations_remain_diagnostic_at_every_duration(self):
        for elapsed in (9.6, 129.9, 130, 600):
            data = sample(elapsed_seconds=elapsed, warnings=[warning(c) for c in ("unjoined_observed", "unjoined_traffic", "snooping")])
            result = assess(data)
            self.assertEqual(result["health_status"], "HEALTHY")
            self.assertEqual(result["actionable_findings"], [])
            self.assertEqual(result["diagnostic_information"], data["warnings"])

    def test_supplied_low_bandwidth_example_is_not_attention(self):
        data = sample(elapsed_seconds=9.6, total_mbps=.190, querier_detected=False, queriers=[],
                      streams=[dict(group="224.0.1.129", mbps=.074)],
                      ptp_domains=[0], ptp_sources=[dict(active=True, announce_active=True)],
                      warnings=[warning("unjoined_observed")])
        result = assess(data)
        self.assertEqual(result["health_status"], "HEALTHY")
        self.assertEqual(result["health_confidence"], "observing")
        self.assertEqual(result["health_checks"]["igmp"]["status"], "OBSERVING")
        self.assertEqual(result["actionable_findings"], [])

    def test_independent_load_evidence_still_prompts_review(self):
        result = assess(sample(total_mbps=20, warnings=[warning("unjoined_traffic"), warning("high_traffic")]))
        self.assertEqual(result["health_status"], "ATTENTION")
        self.assertEqual([f["code"] for f in result["actionable_findings"]], ["high_traffic"])
        self.assertEqual([w["code"] for w in result["diagnostic_information"]], ["unjoined_traffic"])

    def test_sustained_bandwidth_requires_all_complete_seconds(self):
        buckets = [[second, 100, 37_500_000] for second in range(100, 130)]
        self.assertEqual(sustained_bandwidth(buckets, 130.5), 300)
        self.assertEqual(sustained_bandwidth(buckets[:-1], 130.5), 0)
        self.assertEqual(sustained_bandwidth(buckets + [[130, 1, 999999999]], 131), 300)
        self.assertEqual(sustained_bandwidth(buckets, 160), 0)

    def test_substantial_unjoined_bandwidth_uses_one_severity_decision(self):
        group = dict(address="239.1.1.1", membership_known=True, joined=False, mbps=300, sustained_min_mbps=300)
        data = sample(groups=[group], total_mbps=300)
        result = apply_health(data)
        self.assertEqual(result["health_status"], "ATTENTION")
        self.assertEqual(group["assessment"], "Possible flooding")
        self.assertTrue(group["suspected_flood"])
        self.assertIn("possible_flooding", {w["code"] for w in result["warnings"] if w["severity"] == "warning"})
        self.assertEqual(result, apply_health(copy.deepcopy(result)))
        for field, value in (("membership_known", False), ("joined", True), ("sustained_min_mbps", 0)):
            modified = copy.deepcopy(data)
            modified["groups"][0][field] = value
            self.assertNotIn("possible_flooding", {f["code"] for f in apply_health(modified)["actionable_findings"]})
        data["elapsed_seconds"] = 99
        self.assertNotIn("possible_flooding", {f["code"] for f in apply_health(data)["actionable_findings"]})
        self.assertEqual(group["assessment"], "Observed")

    def test_query_wait_requires_duration_and_applicability(self):
        data = sample(querier_detected=False, queriers=[], streams=[dict(group="239.1.1.1")])
        data["elapsed_seconds"] = 129.9
        self.assertEqual(assess(data)["health_status"], "HEALTHY")
        self.assertEqual(assess(data)["health_confidence"], "observing")
        self.assertIn("Waiting", assess(data)["health_checks"]["igmp"]["summary"])
        data["elapsed_seconds"] = 130
        self.assertEqual(assess(data)["health_status"], "PROBLEM")
        data["streams"] = [dict(group="224.0.0.251")]
        self.assertEqual(assess(data)["health_status"], "HEALTHY")
        data["streams"] = [dict(group="239.1.1.1")]
        data["igmp_counts"] = {}
        self.assertEqual(assess(data)["health_status"], "HEALTHY")

    def test_empty_capture_is_not_conclusive(self):
        result = assess(sample(packets=0, queriers=[], querier_detected=False, igmp_counts={}))
        self.assertEqual(result["health_confidence"], "incomplete")
        self.assertIn("not yet conclusive", result["health_message"])
        self.assertEqual(result["actionable_findings"], [])
        self.assertEqual(result["health_checks"]["rtp"]["status"], "NOT_OBSERVED")

    def test_capture_failure_does_not_claim_missing_querier(self):
        result = assess(sample(error="Permission denied", querier_detected=False, streams=[dict(group="239.1.1.1")]))
        self.assertEqual(result["health_status"], "ATTENTION")
        self.assertEqual(result["health_confidence"], "incomplete")
        self.assertEqual([f["code"] for f in result["actionable_findings"]], ["capture"])

    def test_marking_change_requires_review_not_proven_timing_fault(self):
        result = assess(sample(warnings=[warning("ptp_dscp")]))
        self.assertEqual(result["health_status"], "ATTENTION")
        self.assertEqual(result["health_checks"]["ptp"]["status"], "ATTENTION")

    def test_report_count_does_not_establish_instability(self):
        self.assertEqual(assess(sample(igmp_counts=dict(report=1000000)))["health_status"], "HEALTHY")
        self.assertEqual(assess(sample(warnings=[warning("igmp_churn", transitions=6)]))["health_status"], "ATTENTION")
        self.assertEqual(assess(sample(warnings=[warning("igmp_churn", transitions=20)]))["health_status"], "PROBLEM")

    def test_load_without_capacity_is_not_a_confirmed_storm(self):
        self.assertEqual(assess(sample(total_mbps=100, warnings=[warning("high_traffic")]))["health_status"], "ATTENTION")

    def test_significant_rtp_loss_thresholds(self):
        rtp = dict(packets=100, estimated_missing_packets=20)
        data = sample(streams=[dict(group="239.1.1.1", rtp=[rtp])])
        self.assertEqual(assess(data)["health_status"], "PROBLEM")
        rtp["packets"] = 99
        self.assertEqual(assess(data)["health_status"], "ATTENTION")
        rtp.update(packets=10000, estimated_missing_packets=20)
        self.assertEqual(assess(data)["health_status"], "ATTENTION")
        rtp["estimated_missing_packets"] = 0
        self.assertEqual(assess(data)["health_status"], "HEALTHY")

    def test_health_does_not_mutate_or_remove_raw_data(self):
        data = sample(warnings=[warning("best_effort", sources=["10.0.0.1"]), warning("mixed_dscp")])
        before = copy.deepcopy(data)
        json.dumps(assess(data), allow_nan=False)
        self.assertEqual(before, data)

    def test_page_keeps_technical_tables_collapsed_and_ids_unique(self):
        from html.parser import HTMLParser
        from unittest.mock import patch
        from app import app
        from http_test_support import authorized_client

        class Structure(HTMLParser):
            def __init__(self):
                super().__init__()
                self.details = []
                self.ids = {}
                self.duplicates = []

            def handle_starttag(self, tag, attributes):
                attrs = dict(attributes)
                if tag == "details":
                    self.details.append("open" in attrs)
                if "id" in attrs:
                    if attrs["id"] in self.ids:
                        self.duplicates.append(attrs["id"])
                    self.ids[attrs["id"]] = list(self.details)

            def handle_endtag(self, tag):
                if tag == "details":
                    self.details.pop()

        with patch("routes.get_nics", return_value=[]):
            response = authorized_client(app).get("/multicast")
        parser = Structure()
        parser.feed(response.get_data(as_text=True))
        self.assertEqual(parser.duplicates, [])
        self.assertEqual(parser.details, [])
        for element in ("multicastStreams", "igmpEvents", "ptpSources", "dscpDistribution", "rtpSummary", "multicastWarnings", "floodingObservations"):
            self.assertTrue(parser.ids[element], element)
            self.assertFalse(any(parser.ids[element]), element)
        for element in ("healthFindings", "networkHealth", "groupedStreams"):
            self.assertEqual(parser.ids[element], [])

    def test_export_has_consistent_summary_then_all_details(self):
        data = sample(warnings=[warning("unjoined_observed"), warning("possible_ptp"), warning("ptp_dscp")],
                      unrecognized_ptp_candidates=3332, igmp_events=[dict(source="192.168.1.1")])
        report = _multicast_report_content(data)
        self.assertTrue(report.startswith("NETWORK HEALTH SUMMARY"))
        self.assertIn("Overall: ATTENTION", report)
        summary, details = report.split("TECHNICAL DETAILS", 1)
        self.assertNotIn("possible_ptp", summary)
        self.assertIn('"unrecognized_ptp_candidates": 3332', details)
        self.assertIn('"igmp_events"', details)
        self.assertIn('"possible_ptp"', details)


if __name__ == "__main__":
    unittest.main()
