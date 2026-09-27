"""Compare per-Observation Top-K maps with Category-pool batch reconciliation."""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import load_env
from scripts.experiment_vector_issue_recall import expanded_observations
from scripts.group_dinner_date_observations import BASELINE_SHA256
import vector_issue_experiment as vector

BASE_ISSUES = ROOT / "evaluation/dinner-date-issue-grouping/run-20260912T160614396580Z.json"
EMBEDDING_CACHE = ROOT / "evaluation/dinner-date-multilingual-issue-match/embeddings-minilm-v2.3-100.json"
OLD_RESULT = ROOT / "evaluation/dinner-date-multilingual-issue-match/run-20260913T061641574280Z.json"
FOLDER = ROOT / "evaluation/dinner-date-batch-issue-reconciliation"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    dataset, records, _, _, frozen = expanded_observations(model)
    observations = vector.observations_from_records(records, dataset)
    frozen_ids = {row["observation_id"] for row in frozen}
    new_observations = [row for row in observations if row["observation_id"] not in frozen_ids]
    by_observation = {row["observation_id"]: row for row in observations}

    base = _load(BASE_ISSUES)
    base_issues = base["issues"]
    if (base["baseline"]["sha256"] != BASELINE_SHA256 or
            {oid for issue in base_issues for oid in issue["observation_ids"]} != frozen_ids):
        raise ValueError("Base Issues do not match the frozen baseline")
    embedded = _load(EMBEDDING_CACHE)
    if (embedded.get("model") != vector.MULTILINGUAL_MODEL or
            set(embedded.get("vectors", {})) != set(by_observation)):
        raise ValueError("Embedding cache does not match the 59 Observations")

    pools = vector.category_candidate_pools(new_observations, base_issues, embedded["vectors"])
    calls = []
    reconciliation = vector.reconcile_category_pools(pools, observations, base_issues, calls.append)
    final_issues = vector.apply_batch_reconciliation(base_issues, observations, reconciliation)
    old = _load(OLD_RESULT)

    raw_candidates = sum(pool["raw_candidate_count"] for pool in pools)
    unique_candidates = sum(len(pool["candidate_issue_ids"]) for pool in pools)
    existing_assignments = [row for row in reconciliation["assignments"]
                            if row["action"] == "existing_issue"]
    new_assignments = [row for row in reconciliation["assignments"] if row["action"] == "new_issue"]
    usage = Counter()
    for call in calls:
        usage.update(call.get("usage", {}))
    old_summary = old["summary"]
    summary = {
        "new_observations": len(new_observations),
        "category_buckets": len(pools),
        "category_observation_counts": dict(sorted(Counter(
            row["category"] for row in new_observations).items())),
        "exact_observation_groups": sum(len(pool["observation_groups"]) for pool in pools),
        "raw_topk_candidates": raw_candidates,
        "deduplicated_candidate_issues": unique_candidates,
        "candidate_context_removed": raw_candidates - unique_candidates,
        "candidate_context_reduction_percent": round(
            (raw_candidates - unique_candidates) / raw_candidates * 100, 1) if raw_candidates else 0,
        "llm_calls": len(calls),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "total_tokens": usage["total_tokens"],
        "existing_issue_observations": len(existing_assignments),
        "new_issue_observations": len(new_assignments),
        "new_issues": len(reconciliation["new_issues"]),
        "final_issues": len(final_issues),
        "cross_category_assignments": 0,
    }
    comparison = {
        "old_route": {"new_observations": old_summary["new_observations"],
                      "llm_calls": old_summary["llm_calls"],
                      "prompt_tokens": old_summary["llm_prompt_tokens"],
                      "completion_tokens": old_summary["llm_total_tokens"] - old_summary["llm_prompt_tokens"],
                      "total_tokens": old_summary["llm_total_tokens"],
                      "existing_issue_observations": old_summary["matched_existing"],
                      "new_issues": old_summary["created_new"], "final_issues": old_summary["final_issues"]},
        "batch_route": {key: summary[key] for key in ("new_observations", "llm_calls", "prompt_tokens",
                                                        "completion_tokens", "total_tokens",
                                                        "existing_issue_observations", "new_issues", "final_issues")},
    }
    comparison["prompt_tokens_saved"] = comparison["old_route"]["prompt_tokens"] - summary["prompt_tokens"]
    comparison["total_tokens_saved"] = comparison["old_route"]["total_tokens"] - summary["total_tokens"]

    query_by_id = {query["observation_id"]: query for pool in pools for query in pool["queries"]}
    pool_by_category = {pool["category"]: pool for pool in pools}
    final_by_observation = {oid: issue for issue in final_issues for oid in issue["observation_ids"]}
    assigned_by_issue = {}
    for row in existing_assignments:
        assigned_by_issue.setdefault(row["issue_id"], []).append(row)
    repeated_existing = max(assigned_by_issue.values(), key=len, default=[])
    existing_example = None
    if len(repeated_existing) > 1:
        category = repeated_existing[0]["category"]
        existing_example = {
            "category": category,
            "observations": [{"observation_id": row["observation_id"],
                              "review_id": by_observation[row["observation_id"]]["review_id"],
                              "description": by_observation[row["observation_id"]]["description"],
                              "top_k": query_by_id[row["observation_id"]]["candidates"],
                              "assignment": row} for row in repeated_existing],
            "candidate_pool": pool_by_category[category]["candidate_issue_ids"],
            "final_issue": final_by_observation[repeated_existing[0]["observation_id"]],
        }
    multi_new = next((row for row in reconciliation["new_issues"]
                      if len(row["observation_ids"]) > 1), None)
    new_example = None
    if multi_new:
        new_example = {**multi_new,
                       "observations": [{"observation_id": oid,
                                         "review_id": by_observation[oid]["review_id"],
                                         "description": by_observation[oid]["description"]}
                                        for oid in multi_new["observation_ids"]],
                       "final_issue": final_by_observation[multi_new["observation_ids"][0]]}
    rejected = [row for row in new_assignments if query_by_id[row["observation_id"]]["candidates"]]
    rejected.sort(key=lambda row: -query_by_id[row["observation_id"]]["candidates"][0]["similarity"])
    rejection_example = None
    if rejected:
        row = rejected[0]
        observation = by_observation[row["observation_id"]]
        rejection_example = {"observation": {key: observation[key] for key in
                                             ("observation_id", "review_id", "category", "description", "evidence")},
                             "top_k": query_by_id[row["observation_id"]]["candidates"],
                             "candidate_pool": pool_by_category[row["category"]]["candidate_issue_ids"],
                             "assignment": row, "final_issue": final_by_observation[row["observation_id"]]}

    result = {
        "purpose": "Category candidate-pool batch Issue reconciliation experiment",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "frozen_baseline_sha256": BASELINE_SHA256,
        "embedding": {"model": vector.MULTILINGUAL_MODEL, "dimensions": vector.MULTILINGUAL_DIMENSIONS,
                      "input_format": "category | object | symptom | description", "top_k": vector.TOP_K,
                      "threshold": None, "similarity_decides_assignment": False},
        "method": {"category_hard_boundary": True, "exact_fingerprint_compression": True,
                   "temporary_similarity_groups": False, "llm_batching": "all explicit Category buckets in one request"},
        "summary": summary, "comparison": comparison, "category_pools": pools,
        "reconciliation": reconciliation, "issues": final_issues, "observations": observations,
        "examples": {"multiple_to_existing": existing_example, "multiple_to_new": new_example,
                     "similar_but_new": rejection_example},
        "api_calls": calls,
    }
    FOLDER.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path = FOLDER / f"run-{stamp}.json"
    report_path = FOLDER / f"run-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Dinner Date Category Batch Issue Reconciliation", "",
             f"- 27 个新增 Observation / {summary['category_buckets']} 个 Category bucket",
             f"- Top-{vector.TOP_K} 原始候选 {raw_candidates} 条；Category pool 去重后 {unique_candidates} 个，"
             f"减少 {summary['candidate_context_removed']} 条（{summary['candidate_context_reduction_percent']}%）",
             f"- LLM {summary['llm_calls']} 次；Prompt {summary['prompt_tokens']}；Completion "
             f"{summary['completion_tokens']}；总 Token {summary['total_tokens']}",
             f"- 匹配历史 Issue {summary['existing_issue_observations']} 条；新建 {summary['new_issues']} 个；"
             f"最终 {summary['final_issues']} 个 Issue", "", "## 与当前路线对照", "",
             "| 路线 | 调用 | Prompt Token | Completion Token | Total Token | Existing | New Issue | Final Issue |",
             "|---|---:|---:|---:|---:|---:|---:|---:|",
             f"| Observation Top-K map | {comparison['old_route']['llm_calls']} | "
             f"{comparison['old_route']['prompt_tokens']} | {comparison['old_route']['completion_tokens']} | "
             f"{comparison['old_route']['total_tokens']} | {comparison['old_route']['existing_issue_observations']} | "
             f"{comparison['old_route']['new_issues']} | {comparison['old_route']['final_issues']} |",
             f"| Category candidate pool | {summary['llm_calls']} | {summary['prompt_tokens']} | "
             f"{summary['completion_tokens']} | {summary['total_tokens']} | "
             f"{summary['existing_issue_observations']} | {summary['new_issues']} | {summary['final_issues']} |", "",
             "## Category candidate pool 去重", "", "| Category | Observation | 原始 Top-K | 去重 Issue |",
             "|---|---:|---:|---:|"]
    lines += [f"| `{pool['category']}` | {len(pool['observation_ids'])} | {pool['raw_candidate_count']} | "
              f"{len(pool['candidate_issue_ids'])} |" for pool in pools]
    lines += ["", "## 多条 Observation → 同一历史 Issue", ""]
    if existing_example:
        lines += [f"- Category：`{existing_example['category']}`；candidate pool："
                  f"`{existing_example['candidate_pool']}`",
                  f"- 最终：`{existing_example['final_issue']['title']}`"]
        lines += [f"- Review `{row['review_id']}`：{row['description']} → {row['assignment']['reason']}"
                  for row in existing_example["observations"]]
    lines += ["", "## 多条 Observation → 同一新 Issue", ""]
    if new_example:
        lines += [f"- 新 Issue：`{new_example['title']}`（`{new_example['final_issue']['issue_id']}`）"]
        lines += [f"- Review `{row['review_id']}`：{row['description']}" for row in new_example["observations"]]
    else:
        lines.append("- 本轮模型没有生成多 Observation 的新 Issue。")
    lines += ["", "## 相似候选但拒绝吸附", ""]
    if rejection_example:
        lines += [f"- Review `{rejection_example['observation']['review_id']}`："
                  f"{rejection_example['observation']['description']}",
                  f"- Top-K：`{json.dumps(rejection_example['top_k'], ensure_ascii=False)}`",
                  f"- 决定：新建 `{rejection_example['final_issue']['title']}`；"
                  f"{rejection_example['assignment']['reason']}"]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary,
                      "prompt_tokens_saved": comparison["prompt_tokens_saved"],
                      "total_tokens_saved": comparison["total_tokens_saved"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
