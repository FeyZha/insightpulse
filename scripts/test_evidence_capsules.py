"""Small runnable checks for deterministic Evidence Capsules."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evidence_capsules as capsules


class EvidenceCapsuleTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"observation_id": "a", "review_id": "r1", "description": "choices do not matter",
             "object": "choices", "symptom": "no_impact", "claim": "complaint"},
            {"observation_id": "b", "review_id": "r1", "description": "same ending",
             "object": "choices", "symptom": "same_outcome", "claim": "complaint"},
            {"observation_id": "c", "review_id": "r2", "description": "no interaction feedback",
             "object": "interaction", "symptom": "no_feedback", "claim": "complaint"},
        ]
        self.issue = {"issue_id": "i1", "observation_ids": ["c", "a", "b"]}
        self.embeddings = {"a": [1.0, 0.0], "b": [0.99, 0.01], "c": [0.0, 1.0]}
        self.meta = {"model": "test", "input_format": "object | symptom | description"}

    def test_capsule_is_complete_deduplicated_and_cacheable(self):
        value = capsules.build_capsule(self.issue, self.rows, self.embeddings, "issue-v1", self.meta, 0.8)
        self.assertEqual([2, 1], [item["observation_count"] for item in value["manifestations"]])
        self.assertEqual(2, value["review_count"])
        self.assertEqual(3, value["observation_count"])
        self.assertEqual(1, value["manifestations"][0]["review_count"])
        with tempfile.TemporaryDirectory() as directory:
            first, first_hit = capsules.cached_capsule(directory, self.issue, self.rows, self.embeddings,
                                                        "issue-v1", self.meta, 0.8)
            second, second_hit = capsules.cached_capsule(directory, self.issue, self.rows, self.embeddings,
                                                          "issue-v1", self.meta, 0.8)
            changed_issue = {"issue_id": "i1", "observation_ids": ["a", "b"]}
            changed, changed_hit = capsules.cached_capsule(directory, changed_issue, self.rows[:2],
                                                            self.embeddings, "issue-v1", self.meta, 0.8)
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertEqual(first, second)
        self.assertFalse(changed_hit)
        self.assertEqual(2, changed["observation_count"])

    def test_validation_rejects_duplicate_membership(self):
        value = capsules.build_capsule(self.issue, self.rows, self.embeddings, "issue-v1", self.meta, 0.8)
        value["manifestations"][1]["observation_ids"].append("a")
        with self.assertRaisesRegex(ValueError, "exactly once"):
            capsules.validate_capsule(value, self.issue, self.rows, "issue-v1", self.meta, 0.8)

    def test_readable_label_is_short_and_keeps_real_observation_text(self):
        row = {"description": "玩家表示即使最高画质设置下，角色看起来仍然像纸糊的一样。",
               "object": "character", "symptom": "poor_rendering"}
        label = capsules.readable_label(row)
        self.assertLessEqual(len(label), 24)
        self.assertNotIn("玩家表示", label)
        self.assertIn("最高画质", label)


if __name__ == "__main__":
    unittest.main()
