"""Offline checks for the Decision Capsule A/B contract."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_decision_capsule_ab as experiment


class DecisionCapsuleAbTest(unittest.TestCase):
    def test_output_requires_traceable_observation_ids(self):
        candidates = [{"id": "issue-1"}]
        row = {"issue_id": "issue-1", "type": "investigate", "title": "核查问题边界",
               "action": "梳理当前表现并确认需要验证的产品环节", "unknown": "目前缺少产品内行为数据",
               "validation_focus": "确认行动是否减少相同用户困难",
               "basis_dimensions": ["coverage"], "rationale": "当前证据支持先核查问题边界",
               "evidence_observation_ids": ["observation-1"]}
        gates = {"issue-1": {"allowed_actions": ["investigate", "validate", "monitor"]}}
        result = experiment._validate(json.dumps({"decisions": [row]}, ensure_ascii=False),
                                      candidates, {"issue-1": {"observation-1"}}, gates)
        self.assertEqual(["observation-1"], result[0]["evidence_observation_ids"])
        row["evidence_observation_ids"] = ["not-real"]
        with self.assertRaisesRegex(ValueError, "无效证据"):
            experiment._validate(json.dumps({"decisions": [row]}, ensure_ascii=False),
                                 candidates, {"issue-1": {"observation-1"}}, gates)


if __name__ == "__main__":
    unittest.main()
