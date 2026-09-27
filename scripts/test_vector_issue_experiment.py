"""Checks for Category-local vector recall and deterministic Issue assembly."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.group_dinner_date_observations import BASELINE, BASELINE_SHA256, load_frozen_observations
import vector_issue_experiment as v


def observation(oid, category, description, review_id=None):
    return {"observation_id": oid, "review_id": review_id or "r" + oid, "category": category,
            "object": "player_actions", "symptom": "input_has_no_effect",
            "description": description, "evidence": description, "claim": "complaint",
            "change_evidence": ""}


class VectorIssueChecks(unittest.TestCase):
    def test_01_embedding_input_uses_only_four_canonical_fields(self):
        row = dict(observation("a", "controls_input", "操作没有影响"), review_text="全文", severity="high")
        self.assertEqual(v.embedding_text(row),
                         "controls_input | player_actions | input_has_no_effect | 操作没有影响")
        self.assertNotIn("全文", v.embedding_text(row))
        self.assertNotIn("severity", v.embedding_text(row))

    def test_02_topk_is_sorted_and_never_crosses_category(self):
        rows = [observation("a", "controls_input", "a"), observation("b", "controls_input", "b"),
                observation("c", "controls_input", "c"), observation("x", "visual_graphics", "x")]
        vectors = {"a": {0: 1.0}, "b": {0: 0.8, 1: 0.6},
                   "c": {0: 0.6, 1: 0.8}, "x": {0: 1.0}}
        result = {row["observation_id"]: row for row in v.topk_neighbors(rows, vectors, k=2)}
        self.assertEqual([row["observation_id"] for row in result["a"]["neighbors"]], ["b", "c"])
        self.assertEqual(len(result["a"]["neighbors"]), 2)
        self.assertEqual(result["x"]["neighbors"], [])
        self.assertEqual(v.topk_for_observation(rows[0], rows[1:], vectors, k=1)["neighbors"][0]
                         ["observation_id"], "b")

    def test_03_similarity_alone_never_merges(self):
        rows = [observation("a", "controls_input", "选择无效"),
                observation("b", "controls_input", "不能停止独白")]
        decision = {"pair_id": "p", "category": "controls_input", "left_observation_id": "a",
                    "right_observation_id": "b", "similarity": 1.0, "same_issue": False,
                    "reason": "具体行为不同", "title": "", "canonical_description": ""}
        self.assertEqual(len(v.form_issues(rows, [decision])), 2)

    def test_04_cross_category_decision_is_rejected(self):
        rows = [observation("a", "controls_input", "按键无效"),
                observation("b", "visual_graphics", "手部异常")]
        decision = {"pair_id": "p", "category": "controls_input", "left_observation_id": "a",
                    "right_observation_id": "b", "similarity": 0.99, "same_issue": True,
                    "reason": "错误", "title": "错误合并", "canonical_description": "错误合并"}
        with self.assertRaisesRegex(ValueError, "cross-category"):
            v.form_issues(rows, [decision])

    def test_05_issue_coverage_traceability_and_id_are_stable(self):
        rows = [observation("a", "controls_input", "选择不影响剧情", "r1"),
                observation("b", "controls_input", "操作不影响结局", "r2"),
                observation("c", "controls_input", "按键映射别扭", "r3")]
        decision = {"pair_id": "p", "category": "controls_input", "left_observation_id": "a",
                    "right_observation_id": "b", "similarity": 0.8, "same_issue": True,
                    "reason": "都描述选择没有影响", "title": "选择不影响剧情",
                    "canonical_description": "玩家选择不会改变剧情或结局"}
        first, second = v.form_issues(rows, [decision]), v.form_issues(rows, [decision])
        self.assertEqual(first, second)
        self.assertEqual({oid for issue in first for oid in issue["observation_ids"]}, {"a", "b", "c"})
        self.assertEqual(sum(len(issue["observation_ids"]) for issue in first), 3)
        merged = next(issue for issue in first if len(issue["observation_ids"]) == 2)
        self.assertEqual(merged["review_ids"], ["r1", "r2"])
        self.assertNotIn("severity", merged)
        with self.assertRaisesRegex(ValueError, "unique"):
            v.form_issues(rows + [dict(rows[0])], [decision])

    def test_06_frozen_v23_baseline_has_not_drifted(self):
        _, observations = load_frozen_observations()
        self.assertEqual(hashlib.sha256(BASELINE.read_bytes()).hexdigest().upper(), BASELINE_SHA256)
        self.assertEqual(len(observations), 32)

    def test_07_rejected_pair_cannot_be_merged_through_a_third_observation(self):
        rows = [observation("a", "controls_input", "选择无效"),
                observation("b", "controls_input", "互动无效"),
                observation("c", "controls_input", "无法停止独白")]
        base = {"category": "controls_input", "reason": "判断", "title": "控制问题",
                "canonical_description": "具体控制问题"}
        decisions = [dict(base, pair_id="ab", left_observation_id="a", right_observation_id="b",
                          similarity=0.9, same_issue=True),
                     dict(base, pair_id="bc", left_observation_id="b", right_observation_id="c",
                          similarity=0.8, same_issue=True),
                     dict(base, pair_id="ac", left_observation_id="a", right_observation_id="c",
                          similarity=0.7, same_issue=False, title="", canonical_description="")]
        conflicts = []
        issues = v.form_issues(rows, decisions, conflicts=conflicts)
        self.assertEqual(len(issues), 2)
        self.assertFalse(any({"a", "c"}.issubset(issue["observation_ids"]) for issue in issues))
        self.assertEqual(len(conflicts), 1)

    def test_08_issue_candidates_are_same_category_and_sorted(self):
        rows = [observation("a", "controls_input", "选择无效"),
                observation("b", "controls_input", "操作无效"),
                observation("x", "visual_graphics", "手部异常")]
        issues = [{"issue_id": "i1", "category": "controls_input", "observation_ids": ["b"]},
                  {"issue_id": "ix", "category": "visual_graphics", "observation_ids": ["x"]}]
        candidates = v.issue_candidates([rows[0]], issues,
                                        {"a": [1.0, 0.0], "b": [0.8, 0.6], "x": [1.0, 0.0]})
        self.assertEqual(candidates[0]["candidates"], [{"issue_id": "i1", "similarity": 0.8}])

    def test_09_incremental_match_preserves_existing_id_and_creates_local_id(self):
        rows = [observation("a", "controls_input", "旧问题", "r1"),
                observation("b", "controls_input", "同一问题", "r2"),
                observation("c", "audio", "新问题", "r3")]
        existing = [{"issue_id": "stable", "category": "controls_input", "title": "旧问题",
                     "canonical_description": "旧问题", "observation_ids": ["a"], "review_ids": ["r1"]}]
        matches = [{"observation_id": "b", "issue_id": "stable", "reason": "相同", "similarity": 0.9},
                   {"observation_id": "c", "issue_id": None, "reason": "无候选", "similarity": None}]
        first = v.apply_issue_matches(existing, rows, matches)
        second = v.apply_issue_matches(existing, rows, matches)
        self.assertEqual(first, second)
        self.assertEqual(next(issue for issue in first if issue["issue_id"] == "stable")["review_ids"],
                         ["r1", "r2"])
        self.assertFalse(any("severity" in issue for issue in first))

    def test_10_model_cannot_select_a_non_candidate_issue(self):
        queries = [{"observation_id": "a", "category": "controls_input",
                    "candidates": [{"issue_id": "i1", "similarity": 0.8}]}]
        issues = [{"issue_id": "i1", "category": "controls_input"},
                  {"issue_id": "i2", "category": "controls_input"}]
        content = json.dumps({"matches": [{"observation_id": "a", "issue_id": "i2",
                                            "reason": "错误选择"}]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "non-candidate"):
            v.validate_issue_matches(content, queries, issues)


if __name__ == "__main__":
    unittest.main(verbosity=2)
