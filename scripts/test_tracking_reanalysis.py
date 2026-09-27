"""Offline unified analysis of immutable originals; no historical basis required."""
import copy
import tempfile
import unittest
from unittest.mock import patch

import tracking
import tracking_views as views
from scripts.test_issue_tracking import scenario
from scripts.test_tracking import cache, observation

SOURCE = {"label": "TEST FIXTURE fixed definitions", "files": [{"path": "fixture", "sha256": "fixture-only"}]}


def original():
    state = scenario(1, 2, 3, 4)
    state["game"] = "fixture"
    for snap in state["snapshots"]:
        snap.pop("tracking_basis")
    return state


class ReanalysisChecks(unittest.TestCase):
    def test_only_explicit_saved_empty_text_is_excluded_from_denominator(self):
        state = original()
        version = state["snapshots"][0]["reviews"]["1002"]
        version["text"] = ""
        with self.assertRaisesRegex(ValueError, "原文缺失"):
            tracking.prepare_reanalysis(state, state, SOURCE)
        state["snapshots"][0]["excluded_empty_reviews"] = {"1002": version["review_version_id"]}
        derived = tracking.prepare_reanalysis(state, state, SOURCE)
        result = tracking.compare(derived, "a", "b")
        self.assertTrue(result["comparable"], result["reasons"])
        self.assertEqual((result["before"]["total"], result["before"]["analyzed"], result["before"]["excluded"]), (3, 2, 1))
        self.assertEqual(result["issues"][0]["delta_pp"], 0)
        derived["snapshots"][0]["excluded_empty_reviews"]["1001"] = derived["snapshots"][0]["reviews"]["1001"]["review_version_id"]
        self.assertFalse(tracking.compare(derived, "a", "b")["comparable"])

    def test_real_saved_completion_reuses_extraction_but_not_unbound_membership(self):
        from scripts.prepare_tracking_reanalysis import prepare
        with patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("model called")):
            derived = prepare()
        summaries = [tracking._stats(derived, s)[0] for s in derived["snapshots"]]
        self.assertEqual([s["analyzed"] for s in summaries], [100, 830])
        self.assertEqual([s["unassigned"] for s in summaries], [6, 0])
        self.assertEqual(len(derived["issues"]), 56)

    def test_originals_without_historical_analysis_can_be_reanalyzed(self):
        state = original()
        verified = copy.deepcopy(state)
        state["cache"].clear()
        state["assignments"].clear()
        state["snapshots"][0].update(source="github_archive", coverage="bounded_archive", scope=None)
        self.assertFalse(tracking.compare(state, "a", "b")["comparable"])
        derived = tracking.prepare_reanalysis(state, verified, SOURCE)
        result = tracking.compare(derived, "a", "b")
        self.assertTrue(result["comparable"], result["reasons"])
        self.assertEqual((result["before"]["analyzed"], result["after"]["analyzed"]), (3, 4))
        self.assertAlmostEqual(result["issues"][0]["delta_pp"], 100*(2/4-1/3))
        self.assertTrue(result["collection_notes"])
        self.assertEqual(result["analysis"]["label"], "基于历史原文的统一重分析")
        self.assertNotIn("tracking_basis", state["snapshots"][0])

    def test_missing_originals_block_and_scope_conflicts_are_disclosed(self):
        state = original()
        state["snapshots"][0]["reviews"]["1000"]["text"] = ""
        with self.assertRaisesRegex(ValueError, "原文缺失"):
            tracking.prepare_reanalysis(state, state, SOURCE)
        state = original()
        state["snapshots"][0]["scope"]["language"] = "english"
        for snap in state["snapshots"]:
            snap["scope_declaration"] = {"text": "各自时点全量评论", "source": "用户本次任务"}
        result = tracking.compare(tracking.prepare_reanalysis(state, state, SOURCE), "a", "b")
        self.assertTrue(result["comparable"])
        self.assertIn("language", " ".join(result["collection_notes"]))
        self.assertTrue(any("非系统核验" in note for note in result["collection_notes"]))
        with self.assertRaises(ValueError):
            tracking.prepare_reanalysis(state, dict(state, game="another"), SOURCE)

    def test_roundtrip_pins_original_evidence_and_analysis_not_current_cache(self):
        state = original()
        derived = tracking.prepare_reanalysis(state, state, SOURCE)
        scope = dict(before="a", after="b")
        with tempfile.TemporaryDirectory() as root:
            tracking.save_reanalysis(root, derived)
            state["cache"].clear()
            state["issues"].clear()
            with patch.object(tracking.translation, "lookup", return_value={"text_zh": "已保存的新译文", "status": "ready"}):
                restored = tracking.load_reanalysis(root, state)
            self.assertTrue(tracking.compare(restored, "a", "b")["comparable"])
            self.assertEqual(views.evidence(restored, scope, "issue", "before")["items"][0]["text"], "crashes")
            self.assertEqual(views.evidence(restored, scope, "issue", "before")["items"][0]["text_zh"], "已保存的新译文")
            self.assertEqual(views.evidence(restored, scope, "issue", "after")["total"], 2)
            other = dict(state, game="another")
            self.assertIs(tracking.load_reanalysis(root, other), other)
            state["snapshots"][0]["reviews"]["1000"]["text"] = "current text must not replace history"
            loaded = tracking.load_reanalysis(root, state)
            self.assertIn("reanalysis_error", loaded)
            self.assertFalse(tracking.compare(loaded, "a", "b")["comparable"])

    def test_explicit_analysis_uses_fixed_catalog_and_keeps_outside_content(self):
        state = original()
        verified = copy.deepcopy(state)
        version = state["snapshots"][0]["reviews"]["1000"]
        verified["cache"].pop(tracking._cache_key(version))
        derived = tracking.prepare_reanalysis(state, verified, SOURCE)
        self.assertFalse(tracking.compare(derived, "a", "b")["comparable"])
        definitions = copy.deepcopy(derived["issues"])

        def extract(dataset, pending, model, cached, audit):
            self.assertEqual(pending, [version["review_version_id"]])
            cache(derived, version, observation(object="different specific behavior"))
            derived["assignments"].pop(tracking._signature(version["review_id"], observation(object="different specific behavior")))
            return {}, {}, derived["cache"]

        def classify(rows, all_rows, issues, candidates, audit):
            self.assertEqual({r["issue_id"] for r in issues}, set(definitions))
            return [dict(observation_id=r["observation_id"], issue_id=None, reason="not covered") for r in rows]

        with patch.object(tracking.flow, "extract_versions", side_effect=extract), \
                patch.object(tracking.vector, "match_observations_to_issues", side_effect=classify), \
                patch.object(tracking.pipeline, "urlopen", side_effect=AssertionError("network called")):
            tracking.complete_reanalysis(derived)
        result = tracking.compare(derived, "a", "b")
        self.assertTrue(result["comparable"], result["reasons"])
        self.assertEqual(result["before"]["outside_definition"], 1)
        self.assertEqual(result["before"]["analyzed"], 3)
        self.assertEqual(derived["issues"], definitions)
        self.assertEqual(result["issues"][0]["before_count"], 0)

    def test_declarations_are_bound_to_game_and_snapshot_source(self):
        state = original()
        declaration = dict(game="fixture", snapshot_id="a", source_hash=state["snapshots"][0]["source_hash"],
                           text="各自时点全量评论", source="用户本次任务", recorded_at="2026-09-27T00:00:00Z")
        with tempfile.TemporaryDirectory() as root:
            prefs = views.preferences(root)
            prefs["scope_declarations"] = [declaration]
            views.write_preferences(root, prefs)
            loaded = views.tracking_state(root, state)
            self.assertEqual(loaded["snapshots"][0]["scope_declaration"], declaration)
            self.assertIsNone(loaded["snapshots"][1]["scope_declaration"])
            other = views.tracking_state(root, dict(state, game="other", snapshots=state["snapshots"][:1]))
            self.assertIsNone(other["snapshots"][0]["scope_declaration"])
            self.assertEqual(tracking.catalog(other)["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
