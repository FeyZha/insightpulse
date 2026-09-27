"""Offline end-to-end check for the formal Dinner Date command."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_dinner_date_pipeline import build_product_result


class DinnerDatePipelineCheck(unittest.TestCase):
    def test_formal_chain_reaches_traceable_product_result(self):
        result = build_product_result()
        self.assertEqual(result["summary"], {"snapshot_reviews": 830, "analyzed_reviews": 100,
                         "none_reviews": 59, "observations": 59, "issues": 23,
                         "categories_with_observations": 10})
        observation_ids = [oid for issue in result["issues"] for oid in issue["observation_ids"]]
        self.assertEqual(len(observation_ids), len(set(observation_ids)))
        self.assertEqual(len(observation_ids), 59)
        self.assertTrue(all(issue["evidence"] for issue in result["issues"]))
        self.assertNotIn("severity", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["pipeline"]["issue_match_mode"], "verified_reranked_consensus_cache")
        self.assertEqual(result["pipeline"]["rerank_top_k"], 2)
        self.assertEqual(result["pipeline"]["compressed_judgment_units"], 19)
        self.assertEqual(result["pipeline"]["raw_topk_candidates"], 61)
        self.assertEqual(result["pipeline"]["reranked_candidates"], 43)
        self.assertEqual(result["pipeline"]["deduplicated_candidate_issues"], 11)
        self.assertEqual(result["pipeline"]["model_calls_this_run"], 0)

    def test_one_command_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_dinner_date_pipeline.py"),
                 "--output-dir", folder], check=True, capture_output=True, text=True)
            output = json.loads(completed.stdout)
            self.assertTrue(Path(output["json"]).is_file())
            self.assertTrue(Path(output["report"]).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
