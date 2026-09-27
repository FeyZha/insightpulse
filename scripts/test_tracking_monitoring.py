"""Offline multi-point monitoring using the existing fixed-definition fixture."""
import copy
import unittest
import tempfile

import tracking
import tracking_views
from scripts.test_issue_tracking import scenario


class MonitoringChecks(unittest.TestCase):
    def test_latest_difference_reuses_exact_comparison_and_does_not_skip_missing(self):
        state = scenario(412, 413, 3263, 3274)
        latest = tracking.monitoring(state)["latest"]
        expected = tracking.compare(state, "a", "b")
        self.assertEqual(latest["issues"], expected["issues"])
        row = latest["issues"][0]
        self.assertEqual(round(row["before_share"], 1), round(row["after_share"], 1))
        self.assertNotEqual(row["delta_pp"], 0)
        third = copy.deepcopy(state["snapshots"][1])
        third.update(id="c", observed_at=30)
        third.pop("tracking_basis")
        state["snapshots"].append(third)
        latest = tracking.monitoring(state)["latest"]
        self.assertEqual(latest["after"]["id"], "c")
        self.assertFalse(latest["comparable"])
        self.assertEqual(latest["issues"], [])

    def test_new_capture_preserves_saved_history(self):
        state = dict(scenario(1, 2, 10, 10), game="fixture")
        with tempfile.TemporaryDirectory() as root:
            tracking.save_reanalysis(root, state)
            third = copy.deepcopy(state["snapshots"][1])
            third.update(id="c", observed_at=30)
            third.pop("tracking_basis")
            state["snapshots"].append(third)
            loaded = tracking.load_reanalysis(root, state)
            points = tracking.monitoring(loaded)["points"]
            self.assertEqual(len(points), 3)
            self.assertTrue(all(not p["reasons"] for p in points[:2]))
            self.assertTrue(points[2]["reasons"])
            self.assertEqual(tracking_views.monitoring_evidence(loaded, "a", "issue")["total"], 1)
            state["snapshots"][0]["reviews"]["1000"]["text"] = "overwritten"
            self.assertIn("reanalysis_error", tracking.load_reanalysis(root, state))

    def test_three_points_and_evidence(self):
        state = scenario(1, 2, 10, 20)
        third = copy.deepcopy(state["snapshots"][1])
        third.update(id="c", observed_at=30)
        state["snapshots"].append(third)
        data = tracking.monitoring(state)
        self.assertEqual([p["snapshot"]["id"] for p in data["points"]], ["a", "b", "c"])
        self.assertEqual([p["values"]["issue"]["share"] for p in data["points"]], [10, 10, 10])
        for sid, total in (("a", 1), ("b", 2), ("c", 2)):
            evidence = tracking_views.monitoring_evidence(state, sid, "issue")
            self.assertEqual(evidence["total"], total)
            self.assertTrue(all(row["snapshot_id"] == sid and row["text"] == "crashes"
                                for row in evidence["items"]))
        # An incomplete middle point stays present, with no manufactured zero.
        state["snapshots"][1].pop("tracking_basis")
        data = tracking.monitoring(state)
        self.assertEqual(data["points"][1]["values"], {})
        self.assertTrue(data["points"][1]["reasons"])
        self.assertEqual(data["points"][2]["values"]["issue"]["count"], 2)

    def test_single_point_and_zero_mentions(self):
        state = scenario(0, 1, 10, 10)
        self.assertEqual(tracking.monitoring(state)["points"][0]["values"]["issue"]["share"], 0)
        state["snapshots"] = state["snapshots"][1:]
        self.assertEqual(len(tracking.monitoring(state)["points"]), 1)
        self.assertEqual(tracking_views.monitoring_evidence(state, "b", "issue")["total"], 1)

    def test_invalid_points_are_not_connected(self):
        for mutate in (
            lambda s: s["snapshots"][0]["scope"].update(language="schinese"),
            lambda s: s["snapshots"][0]["tracking_basis"].update(definition_ref="changed"),
            lambda s: s["snapshots"][0]["reviews"]["1000"].update(text=""),
            lambda s: s["snapshots"][0].update(app_id=1022980),
        ):
            state = scenario(1, 2, 10, 10)
            mutate(state)
            point = tracking.monitoring(state)["points"][0]
            self.assertTrue(point["reasons"])
            self.assertEqual(point["values"], {})
            with self.assertRaises(ValueError):
                tracking_views.monitoring_evidence(state, "a", "issue")

    def test_current_issue_edits_do_not_change_frozen_history(self):
        state = scenario(1, 2, 10, 10)
        expected = tracking.monitoring(state)
        state["issues"]["issue"]["title"] = "current title changed"
        state["assignments"].clear()
        self.assertEqual(tracking.monitoring(state), expected)


if __name__ == "__main__":
    unittest.main()
