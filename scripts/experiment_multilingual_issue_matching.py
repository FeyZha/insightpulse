"""Match new Dinner Date Observations to frozen Issues with multilingual embeddings."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import load_env
import pipeline
from scripts.experiment_vector_issue_recall import expanded_observations
from scripts.group_dinner_date_observations import BASELINE_SHA256, load_frozen_observations
import vector_issue_experiment as vector

BASE_ISSUES = ROOT / "evaluation/dinner-date-issue-grouping/run-20260912T160614396580Z.json"
FULL_COMPARISON = ROOT / "evaluation/dinner-date-vector-issue/run-20260912T172034157998Z.json"
FOLDER = ROOT / "evaluation/dinner-date-multilingual-issue-match"
EMBEDDING_CACHE = FOLDER / "embeddings-minilm-v2.3-100.json"


def main():
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    dataset, records, extraction_calls, reused_reviews, frozen = expanded_observations(model)
    observations = vector.observations_from_records(records, dataset)
    frozen_ids = {row["observation_id"] for row in frozen}
    new_observations = [row for row in observations if row["observation_id"] not in frozen_ids]

    base = json.loads(BASE_ISSUES.read_text(encoding="utf-8"))
    if base["baseline"]["sha256"] != BASELINE_SHA256:
        raise ValueError("Base Issues do not belong to the frozen Observation baseline")
    base_issues = base["issues"]
    if {oid for issue in base_issues for oid in issue["observation_ids"]} != frozen_ids:
        raise ValueError("Base Issues do not cover the frozen Observations")

    FOLDER.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if EMBEDDING_CACHE.exists():
        cached = json.loads(EMBEDDING_CACHE.read_text(encoding="utf-8"))
        if cached.get("model") != vector.MULTILINGUAL_MODEL or set(cached.get("vectors", {})) != {
                row["observation_id"] for row in observations}:
            raise ValueError("Multilingual embedding cache belongs to another dataset or model")
        embeddings = cached["vectors"]
        embedding_seconds = cached["seconds"]
    else:
        embeddings = vector.encode_multilingual(observations)
        embedding_seconds = round(time.monotonic() - started, 3)
        EMBEDDING_CACHE.write_text(json.dumps({"model": vector.MULTILINGUAL_MODEL,
                                               "dimensions": vector.MULTILINGUAL_DIMENSIONS,
                                               "input_format": "category | object | symptom | description",
                                               "seconds": embedding_seconds, "vectors": embeddings},
                                              ensure_ascii=False), encoding="utf-8")
    if any(len(values) != vector.MULTILINGUAL_DIMENSIONS for values in embeddings.values()):
        raise ValueError("Unexpected multilingual embedding dimensions")

    candidates = vector.issue_candidates(new_observations, base_issues, embeddings)
    calls = []
    matches = vector.match_observations_to_issues(new_observations, observations, base_issues,
                                                   candidates, calls.append)
    final_issues = vector.apply_issue_matches(base_issues, observations, matches)
    comparison = json.loads(FULL_COMPARISON.read_text(encoding="utf-8"))
    matched = [row for row in matches if row["issue_id"]]
    created = [row for row in matches if not row["issue_id"]]
    by_observation = {row["observation_id"]: row for row in observations}
    by_issue = {issue["issue_id"]: issue for issue in base_issues}
    best_match = max(matched, key=lambda row: row["similarity"], default=None)
    best_rejection = max((row for row in created if next(item for item in candidates
                          if item["observation_id"] == row["observation_id"])["candidates"]),
                         key=lambda row: next(item for item in candidates
                             if item["observation_id"] == row["observation_id"])["candidates"][0]["similarity"],
                         default=None)

    def explain(match):
        if not match:
            return None
        row = by_observation[match["observation_id"]]
        query = next(item for item in candidates if item["observation_id"] == match["observation_id"])
        return {"observation": {key: row[key] for key in ("observation_id", "review_id", "category", "object",
                                                               "symptom", "description", "evidence")},
                "candidates": query["candidates"], "decision": match,
                "matched_issue": by_issue.get(match["issue_id"])}

    total_tokens = sum(call.get("usage", {}).get("total_tokens", 0) for call in calls)
    result = {"purpose": "Incremental Observation-to-existing-Issue matching",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "frozen_baseline_sha256": BASELINE_SHA256,
              "embedding": {"model": vector.MULTILINGUAL_MODEL,
                            "dimensions": vector.MULTILINGUAL_DIMENSIONS,
                            "input_format": "category | object | symptom | description",
                            "top_k": vector.TOP_K, "threshold": None, "api_cost": 0,
                            "seconds": embedding_seconds, "cache": str(EMBEDDING_CACHE)},
              "summary": {"reviews": len(records), "observations": len(observations),
                          "existing_observations": len(frozen), "new_observations": len(new_observations),
                          "starting_issues": len(base_issues), "matched_existing": len(matched),
                          "created_new": len(created), "final_issues": len(final_issues),
                          "llm_calls": len(calls), "llm_prompt_tokens": sum(
                              call.get("usage", {}).get("prompt_tokens", 0) for call in calls),
                          "llm_total_tokens": total_tokens, "cross_category_matches": 0},
              "comparison": {"frozen_32_pure_llm_issues": 17,
                             "full_59_pure_llm_issues": comparison["summary"]["pure_llm_issues"],
                             "full_59_observation_pair_issues": comparison["summary"]["vector_candidate_issues"],
                             "incremental_issue_candidate_issues": len(final_issues),
                             "full_pure_llm_calls": comparison["summary"]["pure_group_calls"],
                             "full_pure_llm_tokens": comparison["summary"]["pure_group_total_tokens"],
                             "observation_pair_calls": comparison["summary"]["vector_judge_calls"],
                             "observation_pair_tokens": comparison["summary"]["vector_judge_total_tokens"],
                             "incremental_issue_match_calls": len(calls),
                             "incremental_issue_match_tokens": total_tokens},
              "examples": {"matched_existing": explain(best_match), "created_new": explain(best_rejection)},
              "candidates": candidates, "matches": matches, "issues": final_issues,
              "observations": observations, "api_calls": calls,
              "shared_extraction": {"reused_reviews": reused_reviews,
                                    "calls": len(extraction_calls),
                                    "tokens": sum(call.get("usage", {}).get("total_tokens", 0)
                                                  for call in extraction_calls)}}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, report_path = FOLDER / f"run-{stamp}.json", FOLDER / f"run-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary, compare = result["summary"], result["comparison"]
    lines = ["# Dinner Date 多语言 Embedding → Issue 增量匹配", "",
             f"- 100 Review / {summary['observations']} Observation；历史 {summary['existing_observations']}，"
             f"新增 {summary['new_observations']}",
             f"- 从 {summary['starting_issues']} 个冻结 Issue 开始：匹配已有 {summary['matched_existing']}，"
             f"新建 {summary['created_new']}，最终 {summary['final_issues']} Issue",
             f"- `{vector.MULTILINGUAL_MODEL}` / 384 维 / 同 Category Top-{vector.TOP_K} / 无阈值",
             f"- LLM：{summary['llm_calls']} 次；输入 {summary['llm_prompt_tokens']} Token；"
             f"总计 {summary['llm_total_tokens']} Token", "",
             "## 方法对照", "", "| 方法 | Issue | LLM 调用 | 总 Token |", "|---|---:|---:|---:|",
             f"| 全量纯 LLM | {compare['full_59_pure_llm_issues']} | {compare['full_pure_llm_calls']} | "
             f"{compare['full_pure_llm_tokens']} |",
             f"| Observation pair Top-K | {compare['full_59_observation_pair_issues']} | "
             f"{compare['observation_pair_calls']} | {compare['observation_pair_tokens']} |",
             f"| 新 Observation → Issue Top-K | {compare['incremental_issue_candidate_issues']} | "
             f"{compare['incremental_issue_match_calls']} | {compare['incremental_issue_match_tokens']} |", ""]
    for title, example in (("匹配已有 Issue", result["examples"]["matched_existing"]),
                           ("拒绝候选并新建 Issue", result["examples"]["created_new"])):
        lines += [f"## {title}", ""]
        if example:
            lines += [f"- Review `{example['observation']['review_id']}`：{example['observation']['description']}",
                      f"- 候选：`{json.dumps(example['candidates'], ensure_ascii=False)}`",
                      f"- 决定：`{example['decision']['issue_id']}`；{example['decision']['reason']}", ""]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
