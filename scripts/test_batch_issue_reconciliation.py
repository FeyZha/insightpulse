"""Checks for Category-pool batch Issue reconciliation."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.group_dinner_date_observations import BASELINE, BASELINE_SHA256
import vector_issue_experiment as v


def observation(oid, category="controls_input", description="选择没有影响", review_id=None):
    return {"observation_id": oid, "review_id": review_id or "r" + oid, "category": category,
            "object": "choices", "symptom": "input_has_no_effect", "description": description,
            "evidence": description, "claim": "complaint", "change_evidence": ""}


def issue(iid, category="controls_input", oid="old"):
    return {"issue_id": iid, "category": category, "title": "历史问题",
            "canonical_description": "选择不会影响剧情", "observation_ids": [oid],
            "review_ids": ["r" + oid]}


class BatchIssueChecks(unittest.TestCase):
    def setUp(self):
        self.old = observation("old")
        self.new = [observation("a"), observation("b", description="选项不会改变剧情")]
        self.issues = [issue("i1")]
        self.embeddings = {"old": [1.0, 0.0], "a": [1.0, 0.0], "b": [0.9, 0.1]}
        self.pools = v.category_candidate_pools(self.new, self.issues, self.embeddings)

    def test_pool_deduplicates_topk_and_stays_in_category(self):
        self.assertEqual(self.pools[0]["raw_candidate_count"], 2)
        self.assertEqual(self.pools[0]["candidate_issue_ids"], ["i1"])
        self.assertEqual(self.pools[0]["queries"][0]["candidates"][0]["similarity"], 1.0)
        payload = v.batch_reconciliation_payload(self.pools, [self.old, *self.new], self.issues)
        evidence = {member["evidence"] for group in payload["category_buckets"][0]["observation_groups"]
                    for member in group["members"]}
        self.assertEqual(evidence, {"选择没有影响", "选项不会改变剧情"})

    def test_high_similarity_can_still_create_new_issue(self):
        content = json.dumps({"assignments": [
            {"observation_id": "a", "action": "new_issue", "issue_id": None,
             "new_issue_key": "new_1", "reason": "具体行为不同"},
            {"observation_id": "b", "action": "new_issue", "issue_id": None,
             "new_issue_key": "new_1", "reason": "具体行为相同"}],
            "new_issues": [{"new_issue_key": "new_1", "category": "controls_input",
                            "title": "新问题", "canonical_description": "新的具体问题",
                            "observation_ids": ["a", "b"]}]}, ensure_ascii=False)
        result = v.validate_batch_reconciliation(content, self.pools, self.issues)
        self.assertTrue(all(row["action"] == "new_issue" for row in result["assignments"]))

    def test_missing_duplicate_or_dual_target_is_rejected(self):
        invalid = {"assignments": [
            {"observation_id": "a", "action": "existing_issue", "issue_id": "i1",
             "new_issue_key": "also_new", "reason": "非法"},
            {"observation_id": "a", "action": "existing_issue", "issue_id": "i1",
             "new_issue_key": None, "reason": "重复"}], "new_issues": []}
        with self.assertRaises(ValueError):
            v.validate_batch_reconciliation(json.dumps(invalid, ensure_ascii=False), self.pools, self.issues)

    def test_multiple_observations_share_one_stable_new_issue(self):
        reconciliation = {"assignments": [
            {"observation_id": oid, "category": "controls_input", "action": "new_issue",
             "issue_id": None, "new_issue_key": "new_1", "reason": "相同"} for oid in ("a", "b")],
            "new_issues": [{"new_issue_key": "new_1", "category": "controls_input", "title": "选择无效",
                            "canonical_description": "选择不会改变剧情", "observation_ids": ["a", "b"]}]}
        first = v.apply_batch_reconciliation(self.issues, [self.old, *self.new], reconciliation)
        second = v.apply_batch_reconciliation(self.issues, [self.old, *self.new], reconciliation)
        created = next(row for row in first if row["issue_id"] != "i1")
        self.assertEqual(first, second)
        self.assertEqual(created["observation_ids"], ["a", "b"])
        self.assertEqual(created["review_ids"], ["ra", "rb"])
        self.assertNotIn("issue_id", reconciliation["new_issues"][0])

    def test_cross_category_existing_assignment_is_rejected(self):
        other = issue("audio", "audio", "audio_old")
        content = {"assignments": [
            {"observation_id": "a", "action": "existing_issue", "issue_id": "audio",
             "new_issue_key": None, "reason": "错误"},
            {"observation_id": "b", "action": "existing_issue", "issue_id": "i1",
             "new_issue_key": None, "reason": "正确"}], "new_issues": []}
        with self.assertRaisesRegex(ValueError, "outside its Category pool"):
            v.validate_batch_reconciliation(json.dumps(content, ensure_ascii=False), self.pools,
                                            self.issues + [other])

    def test_exact_duplicates_must_share_a_target(self):
        duplicate = [observation("a"), observation("b")]
        pools = v.category_candidate_pools(duplicate, self.issues,
                                           {"old": [1.0, 0.0], "a": [1.0, 0.0], "b": [1.0, 0.0]})
        content = {"assignments": [
            {"observation_id": "a", "action": "existing_issue", "issue_id": "i1",
             "new_issue_key": None, "reason": "历史"},
            {"observation_id": "b", "action": "new_issue", "issue_id": None,
             "new_issue_key": "new_1", "reason": "新建"}],
            "new_issues": [{"new_issue_key": "new_1", "category": "controls_input", "title": "新问题",
                            "canonical_description": "新问题描述", "observation_ids": ["b"]}]}
        with self.assertRaisesRegex(ValueError, "Exact-duplicate"):
            v.validate_batch_reconciliation(json.dumps(content, ensure_ascii=False), pools, self.issues)

    def test_history_coverage_traceability_and_no_severity(self):
        reconciliation = {"assignments": [
            {"observation_id": "a", "category": "controls_input", "action": "existing_issue",
             "issue_id": "i1", "new_issue_key": None, "reason": "相同"},
            {"observation_id": "b", "category": "controls_input", "action": "existing_issue",
             "issue_id": "i1", "new_issue_key": None, "reason": "相同"}], "new_issues": []}
        result = v.apply_batch_reconciliation(self.issues, [self.old, *self.new], reconciliation)
        self.assertEqual(result[0]["issue_id"], "i1")
        self.assertEqual(result[0]["observation_ids"], ["a", "b", "old"])
        self.assertNotIn("severity", json.dumps(result))

    def test_v23_baseline_hash_is_unchanged(self):
        self.assertEqual(hashlib.sha256(BASELINE.read_bytes()).hexdigest().upper(), BASELINE_SHA256)

    def test_rerank_scores_every_candidate_and_keeps_the_best(self):
        issues = [issue("i1"), issue("i2", oid="old2")]
        rows = [self.old, observation("old2", description="另一个历史问题"), self.new[0]]
        queries = [{"observation_id": "a", "category": "controls_input", "candidates": [
            {"issue_id": "i1", "similarity": 0.9}, {"issue_id": "i2", "similarity": 0.8}]}]
        seen = []

        def scorer(pairs):
            seen.extend(pairs)
            return [0.1, 0.9]

        reranked = v.rerank_issue_candidates(queries, rows, issues, scorer, keep=1)
        self.assertEqual(len(seen), 2)
        self.assertEqual(reranked[0]["candidates"][0]["issue_id"], "i2")

    def test_consensus_group_requires_unanimous_rerank_and_complete_link(self):
        rows = [observation("a"), observation("b"), observation("c")]
        queries = [{"observation_id": oid, "category": "controls_input", "candidates": [
            {"issue_id": "i1" if oid != "c" else "i2", "retrieval_similarity": 0.9,
             "rerank_score": 0.8}]} for oid in ("a", "b", "c")]
        embeddings = {"a": [1.0, 0.0], "b": [0.95, 0.05], "c": [1.0, 0.0]}
        groups = v.consensus_observation_groups(rows, queries, embeddings, min_similarity=0.9)
        self.assertEqual(sorted(len(group["observation_ids"]) for group in groups), [1, 2])
        self.assertEqual(next(group for group in groups if len(group["observation_ids"]) == 2)
                         ["observation_ids"], ["a", "b"])

    def test_group_result_expands_back_to_every_observation(self):
        queries = [{"observation_id": oid, "category": "controls_input", "candidates": [
            {"issue_id": "i1", "retrieval_similarity": 0.9, "rerank_score": 0.8}]}
                   for oid in ("a", "b")]
        groups = v.consensus_observation_groups(
            self.new, queries, {"a": [1.0, 0.0], "b": [0.99, 0.01]}, min_similarity=0.9)
        compressed, compressed_pools = v.compressed_group_pools(groups, self.new, queries)
        self.assertEqual(len(compressed), 1)
        group_id = groups[0]["group_id"]
        grouped = {"assignments": [{"observation_id": group_id, "category": "controls_input",
                                     "action": "existing_issue", "issue_id": "i1",
                                     "new_issue_key": None, "reason": "同一问题"}],
                   "new_issues": []}
        validated = v.validate_batch_reconciliation(json.dumps(grouped, ensure_ascii=False),
                                                     compressed_pools, self.issues)
        original_pools = v.category_pools_from_queries(self.new, queries)
        expanded = v.expand_group_reconciliation(validated, groups, original_pools, self.issues)
        self.assertEqual([row["observation_id"] for row in expanded["assignments"]], ["a", "b"])

    def test_cross_category_model_error_falls_back_to_one_bucket_at_a_time(self):
        audio_old = observation("audio_old", "audio", "声音中断")
        audio_new = observation("audio_new", "audio", "没有声音")
        issues = [*self.issues, issue("audio_issue", "audio", "audio_old")]
        observations = [self.old, audio_old, self.new[0], audio_new]
        pools = v.category_candidate_pools(
            [self.new[0], audio_new], issues,
            {"old": [1.0, 0.0], "a": [1.0, 0.0],
             "audio_old": [0.0, 1.0], "audio_new": [0.0, 1.0]})
        responses = [ValueError("Existing assignment selected outside its Category pool")]
        for pool in pools:
            oid = pool["observation_ids"][0]
            responses.append({"assignments": [{"observation_id": oid, "category": pool["category"],
                                                "action": "existing_issue",
                                                "issue_id": pool["candidate_issue_ids"][0],
                                                "new_issue_key": None, "reason": "相同"}],
                              "new_issues": []})
        with patch.object(v.pipeline, "deepseek", side_effect=responses) as mocked:
            result = v.reconcile_category_pools(pools, observations, issues)
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(len(result["assignments"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
