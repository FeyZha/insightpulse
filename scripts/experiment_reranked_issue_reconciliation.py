"""Try recall -> local rerank -> safe group compression -> one LLM reconciliation."""
from collections import Counter
from datetime import datetime, timezone
import argparse
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
EMBEDDINGS = ROOT / "evaluation/dinner-date-multilingual-issue-match/embeddings-minilm-v2.3-100.json"
OFFICIAL_RESULT = ROOT / "evaluation/dinner-date-batch-issue-reconciliation/run-20260913T074826350134Z.json"
FOLDER = ROOT / "evaluation/dinner-date-reranked-issue-reconciliation"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Run the safe reranked Observation-to-Issue experiment")
    parser.add_argument("--min-group-similarity", type=float, default=0.9)
    args = parser.parse_args()
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    dataset, records, _, _, frozen = expanded_observations(model)
    observations = vector.observations_from_records(records, dataset)
    frozen_ids = {row["observation_id"] for row in frozen}
    new_observations = [row for row in observations if row["observation_id"] not in frozen_ids]

    base = _load(BASE_ISSUES)
    issues = base["issues"]
    if (base["baseline"]["sha256"] != BASELINE_SHA256 or
            {oid for issue in issues for oid in issue["observation_ids"]} != frozen_ids):
        raise ValueError("Base Issues do not match the frozen baseline")
    embedded = _load(EMBEDDINGS)
    if (embedded.get("model") != vector.MULTILINGUAL_MODEL or
            set(embedded.get("vectors", {})) != {row["observation_id"] for row in observations}):
        raise ValueError("Embedding cache does not match the current Observations")
    embeddings = embedded["vectors"]

    recalled = vector.issue_candidates(new_observations, issues, embeddings)
    reranked = vector.rerank_issue_candidates(recalled, observations, issues)
    original_pools = vector.category_pools_from_queries(new_observations, reranked)
    groups = vector.consensus_observation_groups(
        new_observations, reranked, embeddings, min_similarity=args.min_group_similarity)
    compressed, compressed_pools = vector.compressed_group_pools(groups, new_observations, reranked)
    history = [row for row in observations if row["observation_id"] in frozen_ids]
    calls = []
    grouped = vector.reconcile_category_pools(compressed_pools, history + compressed, issues, calls.append)
    reconciliation = vector.expand_group_reconciliation(grouped, groups, original_pools, issues)
    final_issues = vector.apply_batch_reconciliation(issues, observations, reconciliation)
    official = _load(OFFICIAL_RESULT)
    partition = lambda rows: {tuple(sorted(issue["observation_ids"])) for issue in rows}

    usage = Counter()
    for call in calls:
        usage.update(call.get("usage", {}))
    grouped_observations = sum(len(group["observation_ids"]) for group in groups
                               if len(group["observation_ids"]) > 1)
    summary = {
        "new_observations": len(new_observations),
        "embedding_candidates": sum(len(row["candidates"]) for row in recalled),
        "reranked_candidates": sum(len(row["candidates"]) for row in reranked),
        "category_candidate_issues": sum(len(pool["candidate_issue_ids"]) for pool in original_pools),
        "consensus_groups": len(groups),
        "grouped_observations": grouped_observations,
        "largest_group": max(map(lambda group: len(group["observation_ids"]), groups), default=0),
        "llm_assignments": len(grouped["assignments"]),
        "expanded_assignments": len(reconciliation["assignments"]),
        "llm_calls": len(calls),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "total_tokens": usage["total_tokens"],
        "final_issues": len(final_issues),
        "same_issue_partition_as_official": partition(final_issues) == partition(official["issues"]),
        "official_total_tokens": official["summary"]["total_tokens"],
        "total_token_reduction_percent": round(
            (official["summary"]["total_tokens"] - usage["total_tokens"]) /
            official["summary"]["total_tokens"] * 100, 1),
    }
    result = {
        "purpose": "Safe reranked Observation-to-Issue reconciliation experiment",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": {"category_hard_boundary": True, "embedding_top_k": vector.TOP_K,
                   "rerank_model": vector.RERANK_MODEL, "rerank_top_k": vector.RERANK_TOP_K,
                   "group_minimum_similarity": args.min_group_similarity,
                   "complete_link_grouping": True, "unanimous_rerank_required": True,
                   "representatives_per_group": 3, "automatic_assignment": False},
        "summary": summary, "groups": groups, "embedding_recall": recalled,
        "reranked_queries": reranked, "compressed_pools": compressed_pools,
        "group_reconciliation": grouped, "reconciliation": reconciliation,
        "issues": final_issues, "api_calls": calls,
    }
    FOLDER.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, report_path = FOLDER / f"run-{stamp}.json", FOLDER / f"run-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Dinner Date Reranked Issue Reconciliation", "",
             f"- {summary['new_observations']} 个新 Observation：召回 {summary['embedding_candidates']} 个候选，"
             f"精排保留 {summary['reranked_candidates']} 个",
             f"- 安全压缩为 {summary['consensus_groups']} 个判断单元；覆盖 "
             f"{summary['grouped_observations']} 个 Observation；最大组 {summary['largest_group']} 个",
             f"- LLM {summary['llm_calls']} 次 / {summary['total_tokens']} Token；最终 "
             f"{summary['final_issues']} 个 Issue",
             f"- 与正式结果分组一致：{summary['same_issue_partition_as_official']}；总 Token 减少 "
             f"{summary['total_token_reduction_percent']}%", "", "## 分组", "",
             "| Category | Observation | 代表样本 | 一致候选 | 组内最低相似度 |", "|---|---:|---:|---|---:|"]
    lines += [f"| `{group['category']}` | {len(group['observation_ids'])} | "
              f"{len(group['representative_ids'])} | `{group['candidate_issue_id'] or 'none'}` | "
              f"{group['minimum_similarity']} |" for group in groups]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
