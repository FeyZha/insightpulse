"""Offline checks for tracking cache, counts, missing data and snapshot identity."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import data_flow_v2 as flow
import tracking

FILTERS = {"language": "all", "purchase_type": "all", "review_type": "all", "filter_offtopic_activity": 0}


def envelope(sid, at, reviews):
    return {"snapshot": {"snapshot_id": sid, "app_id": 94000, "observed_at": at,
                         "source_type": "steam_api", "coverage": "exhausted", "filters": FILTERS},
            "reviews": reviews}


def review(rid="1", text="crashes", **changes):
    return dict(review_id=rid, text=text, created_at=1, updated_at=2,
                language="english", recommended=True, **changes)


def state_for(*envelopes):
    state = dict(analysis_version=tracking.analysis_version(), model=tracking.MODEL, cache={},
                 assignments={}, issues={}, matches={}, failures={}, snapshots=[], calls=[])
    with tempfile.TemporaryDirectory() as directory:
        data = Path(directory) / "data"
        data.mkdir()
        for i, raw in enumerate(envelopes):
            (data / f"steam-{i}.json").write_text(json.dumps(raw), encoding="utf-8")
        state["snapshots"] = tracking._snapshots(directory, state)
    return state


def observation(evidence="crashes", object="game"):
    return dict(category="stability", object=object, symptom="crash", description="游戏崩溃",
                evidence=evidence, claim="complaint", change_evidence="")


def cache(state, version, *items):
    value = {"categories": [{"category": "stability" if items else "none", "confidence": 1}],
             "observations": list(items)}
    state["cache"][tracking._cache_key(version)] = tracking._valid_extraction(
        version["review_id"], version["text"], value)
    for item in items:
        state["assignments"][tracking._signature(version["review_id"], item)] = "issue"
        state["issues"]["issue"] = dict(issue_id="issue", category="stability", title="游戏崩溃",
                                          canonical_description="游戏运行中崩溃")


class TrackingChecks(unittest.TestCase):
    def test_capture_save_then_analysis_retry_freezes_only_new_complete_snapshots(self):
        from scripts import collect_sample
        from tracking_views import evidence
        old = envelope("legacy", 5, [review("9")])
        raws = [envelope("a", 10, [review(text="crashes before"), review("2", "fine")]),
                envelope("b", 20, [review(text="crashes after"), review("2", "fine"), review("3", "fine")])]
        for raw in raws:
            raw["snapshot"]["tracking_capture_version"] = 1
        initial = state_for(old)
        cache(initial, initial["snapshots"][0]["reviews"]["9"], observation())
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), \
                patch.object(tracking.translation, "translate", return_value={"failed": 0}), \
                patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("model called")), \
                patch.object(tracking.pipeline, "urlopen", side_effect=AssertionError("network called")), \
                patch.object(tracking, "_changes", return_value=[]):
            root = Path(directory)
            tracking._save(root, initial)
            for raw in raws:
                collect_sample.save_capture(root, raw)
            collected = tracking.refresh(root)
            self.assertTrue(all("tracking_basis" not in s for s in collected["snapshots"]))

            def extract(dataset, selected, model, cached, audit):
                for vid in selected:
                    row = dataset["review_versions"][vid]
                    cache(collected, row, *([observation()] if "crashes" in row["text"] else []))
                return {}, {}, collected["cache"]

            with patch.object(flow, "extract_versions", side_effect=extract), \
                    patch.object(tracking, "_assign_new", side_effect=ValueError("interrupted grouping")):
                with self.assertRaisesRegex(ValueError, "interrupted grouping"):
                    tracking.refresh(root, live=True)
            self.assertTrue(all("tracking_basis" not in s for s in tracking._read(tracking._path(root))["snapshots"]))
            with patch.object(flow, "extract_versions", side_effect=AssertionError("repeated extraction")), \
                    patch.object(tracking, "_assign_new", side_effect=lambda state, *_: state["assignments"].update(collected["assignments"])):
                tracking.refresh(root, live=True)
            saved = tracking._read(tracking._path(root))
            self.assertNotIn("tracking_basis", saved["snapshots"][0])
            saved["snapshots"] = tracking._snapshots(root, saved)
            result = tracking.compare(saved, "a", "b")
            self.assertTrue(result["comparable"], result["reasons"])
            self.assertEqual((result["before"]["analyzed"], result["after"]["analyzed"]), (2, 3))
            self.assertAlmostEqual(result["issues"][0]["delta_pp"], 100 * (1 / 3 - 1 / 2))
            scope = dict(before="a", after="b")
            for side, text in (("before", "crashes before"), ("after", "crashes after")):
                self.assertEqual(evidence(saved, scope, "issue", side)["items"][0]["text"], text)
            saved["issues"]["issue"]["canonical_description"] = "current definition changed"
            saved["assignments"].clear()
            self.assertTrue(tracking.compare(saved, "a", "b")["comparable"])
            a = tracking._snapshot(saved, "a")
            saved["cache"][tracking._cache_key(a["reviews"]["1"])]["observations"] = []
            self.assertFalse(tracking.compare(saved, "a", "b")["comparable"])
            with self.assertRaises(ValueError):
                evidence(saved, scope, "issue", "before")

    def test_cache_requires_same_review_text_but_reuses_metadata_changes(self):
        changed = dict(review(), recommended=False, updated_at=3)
        state = state_for(envelope("a", 10, [review()]), envelope("b", 20, [changed]),
                          envelope("c", 30, [review(text="no crash")]))
        versions = [snap["reviews"]["1"] for snap in state["snapshots"]]
        cache(state, versions[0], observation())
        self.assertEqual(len(tracking.observations(state, versions[1])), 1)
        self.assertIsNone(tracking.observations(state, versions[2]))
        self.assertIsNone(tracking.observations(state, dict(versions[0], review_id="2")))

    def test_partial_analysis_cannot_confirm_feedback_removal(self):
        state = state_for(envelope("a", 10, [review()]), envelope("b", 20, [review(text="no crash")]))
        cache(state, state["snapshots"][0]["reviews"]["1"], observation())
        result = tracking.compare(state, "a", "b")
        self.assertEqual(result["after"]["pending"], 1)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["issues"], [])

    def test_issue_counts_distinct_reviews_not_observations(self):
        state = state_for(envelope("a", 10, [review(text="crashes at launch; crashes loading")]))
        snap = state["snapshots"][0]
        cache(state, snap["reviews"]["1"], observation("crashes at launch", "launch"),
              observation("crashes loading", "loading"))
        summary, issues = tracking._stats(state, snap)
        self.assertEqual(summary["observations"], 2)
        self.assertEqual(issues["issue"]["reviews"], 1)
        self.assertEqual(issues["issue"]["complaints"], 1)

    def test_conflicting_identity_or_same_snapshot_state_is_rejected(self):
        with self.assertRaises(ValueError):
            state_for(envelope("a", 10, [review()]),
                      envelope("b", 20, [dict(review(), created_at=0)]))
        with self.assertRaises(ValueError):
            state_for(envelope("a", 10, [review(), review(text="changed")]))

    def test_archived_snapshot_still_protects_review_identity(self):
        state = state_for(envelope("a", 10, [review()]))
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            data.mkdir()
            (data / "steam-new.json").write_text(
                json.dumps(envelope("b", 20, [dict(review(), created_at=0)])), encoding="utf-8")
            with self.assertRaises(ValueError):
                tracking._snapshots(directory, state)

    def test_live_refresh_extracts_matches_groups_and_reuses_saved_results(self):
        first = envelope("a", 10, [review()])
        second = envelope("b", 20, [dict(review(text="crashes on launch"), updated_at=15),
                                    review("2", "crashes on launch"), review("3", "freezes after loading")])
        initial = state_for(first)
        cache(initial, initial["snapshots"][0]["reviews"]["1"], observation())
        prompts = []

        def model(prompt, payload, validator, audit, attempts=2, model=None):
            self.assertEqual(model, tracking.MODEL)
            prompts.append(prompt)
            if prompt == flow.OBSERVATION_PROMPT:
                self.assertEqual(len(payload), 3)
                extracted = []
                for row in payload:
                    item = dict(observation(row["text"], "launch"), description="启动时崩溃")
                    if row["text"].startswith("freezes"):
                        item.update(object="loading", symptom="freeze", description="加载后卡死")
                    extracted.append(dict(id=row["id"], categories=[{"category": "stability", "confidence": 1}],
                                          observations=[item]))
                content = {"reviews": extracted}
            elif prompt == tracking.MATCH_PROMPT:
                self.assertEqual((len(payload["before"]), len(payload["after"])), (1, 1))
                content = {"matches": [{"before_ids": [payload["before"][0]["observation_id"]],
                                        "after_ids": [payload["after"][0]["observation_id"]],
                                        "kind": "details_changed", "confirmed": True,
                                        "reason": "同一崩溃反馈补充了启动条件"}]}
            else:
                self.assertEqual(prompt, tracking.vector.BATCH_RECONCILIATION_PROMPT)
                buckets = payload["category_buckets"]
                self.assertEqual(len(buckets), 1)
                self.assertEqual([issue["issue_id"] for issue in buckets[0]["candidate_issues"]], ["issue"])
                assignments, new_ids = [], []
                for group in buckets[0]["observation_groups"]:
                    new = group["symptom"] == "freeze"
                    for row in group["members"]:
                        assignments.append(dict(observation_id=row["observation_id"],
                                                action="new_issue" if new else "existing_issue",
                                                issue_id=None if new else "issue",
                                                new_issue_key="freeze" if new else None, reason="具体症状对应"))
                        if new:
                            new_ids.append(row["observation_id"])
                self.assertEqual(len(assignments), 2)
                content = {"assignments": assignments, "new_issues": [
                    dict(new_issue_key="freeze", category="stability", title="加载后卡死",
                         canonical_description="游戏加载后画面卡死", observation_ids=new_ids)
                ]}
            result = validator(json.dumps(content, ensure_ascii=False))
            audit({"model": model, "status": "passed", "usage": {"total_tokens": 1}})
            return result

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), \
                patch.object(tracking.translation, "translate", return_value={"total": 0, "ready": 0, "failed": 0, "calls": 0}), \
                patch.object(tracking.pipeline, "urlopen", side_effect=AssertionError("unexpected network")):
            data = Path(directory) / "data"
            data.mkdir()
            for raw in (first, second):
                (data / f"steam-{raw['snapshot']['snapshot_id']}.json").write_text(
                    json.dumps(raw), encoding="utf-8")
            with patch.object(tracking, "_seed", return_value=initial), \
                    patch.object(flow, "extract_versions", wraps=flow.extract_versions) as extract, \
                    patch.object(tracking.pipeline, "deepseek", side_effect=model):
                state = tracking.refresh(directory, live=True)
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(prompts, [flow.OBSERVATION_PROMPT, tracking.MATCH_PROMPT,
                                       tracking.vector.BATCH_RECONCILIATION_PROMPT])
            self.assertEqual((len(state["cache"]), len(state["matches"]), len(state["issues"])), (4, 1, 2))
            summary, counts = tracking._stats(state, state["snapshots"][1])
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(counts["issue"]["reviews"], 2)
            self.assertEqual(sorted(item["reviews"] for item in counts.values()), [1, 2])
            self.assertEqual(tracking._read(tracking._path(directory)), state)
            with patch.object(flow, "extract_versions", side_effect=AssertionError("repeated extraction")), \
                    patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("repeated model call")):
                repeated = tracking.refresh(directory, live=True)
            self.assertEqual(repeated, state)

    def test_real_baseline_loads_offline_with_missing_history_explicit(self):
        with patch.object(tracking.pipeline, "urlopen", side_effect=AssertionError("unexpected network")):
            state = tracking._seed(ROOT)
            state["snapshots"] = tracking._snapshots(ROOT, state)
        summaries = {snap["label"]: snap for snap in tracking.catalog(state)["snapshots"]}
        self.assertEqual(len(state["issues"]), 56)
        self.assertEqual(summaries["steam-94000-current-20260911.json"]["analyzed"], 830)
        self.assertEqual(summaries["steam-94000-history-20250901.json"]["pending"], 6)

    def test_refresh_matches_nonadjacent_versions_even_when_middle_review_is_missing(self):
        texts = ("crashes", "application crashes", "game crashes")
        for missing_middle in (False, True):
            with self.subTest(missing_middle=missing_middle), tempfile.TemporaryDirectory() as directory, \
                    patch.object(tracking.translation, "translate", return_value={"total": 0, "ready": 0, "failed": 0, "calls": 0}):
                raws = [envelope(sid, at, [] if missing_middle and sid == "b" else [review(text=text)])
                        for sid, at, text in zip(("a", "b", "c"), (10, 20, 30), texts)]
                initial = state_for(*raws)
                for snap in initial["snapshots"]:
                    for version in snap["reviews"].values():
                        cache(initial, version, observation(version["text"]))
                first = initial["snapshots"][0]["reviews"]["1"]
                initial["assignments"] = {tracking._signature("1", observation(first["text"])): "issue"}
                data = Path(directory) / "data"
                data.mkdir()
                for raw in raws:
                    (data / f"steam-{raw['snapshot']['snapshot_id']}.json").write_text(
                        json.dumps(raw), encoding="utf-8")
                pairs = []

                def matcher(prompt, payload, validator, audit, attempts=2, model=None):
                    self.assertEqual(prompt, tracking.MATCH_PROMPT)
                    self.assertEqual(model, tracking.MODEL)
                    pairs.append((payload["before_text"], payload["after_text"]))
                    value = {"matches": [{"before_ids": [payload["before"][0]["observation_id"]],
                                           "after_ids": [payload["after"][0]["observation_id"]],
                                           "kind": "continued", "confirmed": True,
                                           "reason": "同一崩溃反馈仅措辞不同"}]}
                    result = validator(json.dumps(value, ensure_ascii=False))
                    audit({"model": model, "status": "passed", "usage": {"total_tokens": 1}})
                    return result

                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), \
                        patch.object(flow, "extract_versions", side_effect=AssertionError("cached text was re-extracted")):
                    with patch.object(tracking, "_seed", return_value=initial), \
                            patch.object(tracking.pipeline, "deepseek", side_effect=matcher):
                        state = tracking.refresh(directory, live=True)
                    expected = {(texts[0], texts[2])} if missing_middle else {
                        (texts[0], texts[1]), (texts[0], texts[2]), (texts[1], texts[2])}
                    self.assertEqual(set(pairs), expected)
                    self.assertEqual(len(pairs), len(expected))
                    self.assertEqual(set(state["assignments"].values()), {"issue"})
                    self.assertEqual(len(state["assignments"]), 2 if missing_middle else 3)
                    self.assertEqual(tracking._changes(state, state["snapshots"][0]["reviews"]["1"], state["snapshots"][-1]["reviews"]["1"])[0]["kind"], "continued")
                    with patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("repeated model call")):
                        self.assertEqual(tracking.refresh(directory, live=True), state)

    def test_old_extractions_cannot_be_relabelled_with_a_changed_prompt(self):
        with patch.object(flow, "OBSERVATION_PROMPT", flow.OBSERVATION_PROMPT + "\nChanged extraction rules"), \
                self.assertRaisesRegex(ValueError, "已有分析口径不一致"):
            tracking._seed(ROOT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
