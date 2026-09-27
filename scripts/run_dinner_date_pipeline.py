"""Build the formal Dinner Date product result from the verified v2.3 pipeline cache."""
from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import data_flow_v2 as flow
import pipeline
from scripts.group_dinner_date_observations import BASELINE, BASELINE_SHA256
import vector_issue_experiment as vector

SOURCE = ROOT / "data/steam-94000-current-20260911.json"
OBSERVATION_CACHE = ROOT / "evaluation/dinner-date-vector-issue/observations-v2.3-100.json"
BASE_ISSUES = ROOT / "evaluation/dinner-date-issue-grouping/run-20260912T160614396580Z.json"
EMBEDDING_CACHE = ROOT / "evaluation/dinner-date-multilingual-issue-match/embeddings-minilm-v2.3-100.json"
BATCH_MATCH_CACHE = ROOT / "evaluation/dinner-date-reranked-issue-reconciliation/run-20260914T074105211160Z.json"
OUTPUT = ROOT / "runtime/exports"
SAMPLE_SIZE = 100
PRODUCT_VERSION = "dinner-date-product-v1.2"
CACHE_HASHES = {
    OBSERVATION_CACHE: "890E140EF327B4BD26FA9F72EBAF8321B62F70FDC6BD20B99439831BD7E037AD",
    BASE_ISSUES: "6A14AAB0B95405EDF9F447E0FF6C07465D845E19D903F567207F5ADC07CD38F8",
    EMBEDDING_CACHE: "D6934FFD4B170098CA167487096CB8326E0E678D3F5E20066BD2CC4567A8D1CE",
    BATCH_MATCH_CACHE: "D9C28BCAED6AC068E6E7B0288022B7BD330F3EB3226273E9B3C229B8264C3B75",
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_review_ids(dataset, baseline):
    by_review = {row["review_id"]: row for row in dataset["review_versions"].values()}
    selected = {row["review_id"] for row in baseline["reviews"]}
    for recommended in (True, False):
        missing = SAMPLE_SIZE // 2 - sum(by_review[rid]["recommended"] == recommended for rid in selected)
        candidates = (rid for rid, row in by_review.items()
                      if row["recommended"] == recommended and rid not in selected)
        selected.update(sorted(candidates, key=pipeline.fingerprint)[:missing])
    if len(selected) != SAMPLE_SIZE:
        raise ValueError("Dinner Date source cannot supply the frozen balanced sample")
    return selected


def build_product_result():
    if hashlib.sha256(BASELINE.read_bytes()).hexdigest().upper() != BASELINE_SHA256:
        raise ValueError("Frozen v2.3 Observation baseline changed")
    for path, expected_hash in CACHE_HASHES.items():
        if hashlib.sha256(path.read_bytes()).hexdigest().upper() != expected_hash:
            raise ValueError(f"Verified pipeline cache changed: {path.name}")
    snapshot = _load(SOURCE)
    if snapshot.get("snapshot", {}).get("app_id") != flow.APP_ID:
        raise ValueError("Formal pipeline only accepts Dinner Date App 94000")
    dataset = flow.load_snapshots([snapshot])
    baseline = _load(BASELINE)
    records_cache = _load(OBSERVATION_CACHE)
    model = records_cache.get("model")
    expected = (BASELINE_SHA256, flow.PROMPT_VERSION, SAMPLE_SIZE)
    actual = (records_cache.get("baseline_sha256"), records_cache.get("prompt_version"),
              records_cache.get("review_count"))
    if actual != expected or not isinstance(model, str):
        raise ValueError("Observation cache does not match the frozen v2.3 contract")

    records = records_cache.get("records", [])
    selected = _selected_review_ids(dataset, baseline)
    if {row.get("review_id") for row in records} != selected:
        raise ValueError("Observation cache does not cover the frozen 100-Review sample")
    observations = vector.observations_from_records(records, dataset)
    by_observation = {row["observation_id"]: row for row in observations}

    base = _load(BASE_ISSUES)
    frozen_ids = {row["observation_id"] for row in base.get("observations", [])}
    if (base.get("baseline", {}).get("sha256") != BASELINE_SHA256 or
            {oid for issue in base.get("issues", []) for oid in issue["observation_ids"]} != frozen_ids):
        raise ValueError("Base Issues do not cover the frozen Observation baseline")
    base_issues = base["issues"]
    new_observations = [row for row in observations if row["observation_id"] not in frozen_ids]

    embedded = _load(EMBEDDING_CACHE)
    if (embedded.get("model"), embedded.get("dimensions"), set(embedded.get("vectors", {}))) != (
            vector.MULTILINGUAL_MODEL, vector.MULTILINGUAL_DIMENSIONS, set(by_observation)):
        raise ValueError("Embedding cache does not match the current Observations")
    pools = vector.category_candidate_pools(new_observations, base_issues, embedded["vectors"])

    cached_matches = _load(BATCH_MATCH_CACHE)
    method = cached_matches.get("method", {})
    if (method.get("rerank_model"), method.get("rerank_top_k"),
            method.get("unanimous_rerank_required"), method.get("automatic_assignment")) != (
            vector.RERANK_MODEL, vector.RERANK_TOP_K, True, False):
        raise ValueError("Issue-match cache does not use the verified safe rerank contract")
    reconciliation = vector.validate_batch_reconciliation(
        json.dumps(cached_matches.get("reconciliation"), ensure_ascii=False), pools, base_issues)
    issues = vector.apply_batch_reconciliation(base_issues, observations, reconciliation)

    covered = [oid for issue in issues for oid in issue["observation_ids"]]
    if len(covered) != len(set(covered)) or set(covered) != set(by_observation):
        raise ValueError("Final Issues must cover every Observation exactly once")

    review_categories = Counter(category["category"] for row in records for category in row["categories"])
    observation_categories = Counter(row["category"] for row in observations)
    issue_categories = Counter(row["category"] for row in issues)
    none_reviews = sum(len(row["categories"]) == 1 and row["categories"][0]["category"] == "none"
                       for row in records)
    category_rows = [{"category": category, "review_count": review_categories[category],
                      "observation_count": observation_categories[category],
                      "issue_count": issue_categories[category]}
                     for category in flow.CATEGORIES if review_categories[category] or
                     observation_categories[category] or issue_categories[category]]
    product_issues = []
    for issue in issues:
        evidence = [{"observation_id": oid, "review_id": by_observation[oid]["review_id"],
                     "description": by_observation[oid]["description"],
                     "evidence": by_observation[oid]["evidence"]}
                    for oid in issue["observation_ids"]]
        product_issues.append({**issue, "observation_count": len(issue["observation_ids"]),
                               "review_count": len(issue["review_ids"]), "evidence": evidence})
    product_issues.sort(key=lambda row: (-row["review_count"], row["category"], row["issue_id"]))

    review_results = []
    observations_by_review = {}
    for row in observations:
        observations_by_review.setdefault(row["review_id"], []).append(row["observation_id"])
    for row in records:
        review_results.append({"review_id": row["review_id"], "language": row["language"],
                               "recommended": row["recommended"], "categories": row["categories"],
                               "observation_ids": sorted(observations_by_review.get(row["review_id"], []))})

    return {
        "schema_version": PRODUCT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product": "InsightPulse",
        "game": {"name": "Dinner Date", "app_id": flow.APP_ID},
        "scope": "current_snapshot_balanced_sample",
        "source": {"file": str(SOURCE), "snapshot_id": snapshot["snapshot"]["snapshot_id"],
                   "observed_at": snapshot["snapshot"]["observed_at"],
                   "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest()},
        "pipeline": {
            "path": "Steam Snapshot → Review → Category → Observation → Issue → 产品结果输出",
            "processing_version": flow.PROCESSING_VERSION,
            "observation_prompt_version": flow.PROMPT_VERSION,
            "observation_model": model,
            "embedding_model": vector.MULTILINGUAL_MODEL,
            "embedding_input": "category | object | symptom | description",
            "top_k": vector.TOP_K,
            "similarity_threshold": None,
            "rerank_model": vector.RERANK_MODEL,
            "rerank_top_k": vector.RERANK_TOP_K,
            "group_minimum_similarity": method["group_minimum_similarity"],
            "issue_match_mode": "verified_reranked_consensus_cache",
            "category_buckets": len(pools),
            "raw_topk_candidates": cached_matches["summary"]["embedding_candidates"],
            "reranked_candidates": cached_matches["summary"]["reranked_candidates"],
            "deduplicated_candidate_issues": cached_matches["summary"]["category_candidate_issues"],
            "compressed_judgment_units": cached_matches["summary"]["llm_assignments"],
            "model_calls_this_run": 0,
            "tokens_this_run": 0,
        },
        "summary": {"snapshot_reviews": len(dataset["review_versions"]), "analyzed_reviews": len(records),
                    "none_reviews": none_reviews, "observations": len(observations), "issues": len(issues),
                    "categories_with_observations": len(observation_categories)},
        "categories": category_rows,
        "issues": product_issues,
        "review_results": sorted(review_results, key=lambda row: pipeline.fingerprint(row["review_id"])),
        "limitations": ["结果来自固定的 100 条平衡样本，不代表 830 条评论的总体比例。",
                        "当前快照没有真实游戏更新事件，因此不输出更新前后趋势。",
                        "Issue 按出现 Review 数展示，不等同于严重性或优先级。"],
    }


def render_markdown(result):
    summary = result["summary"]
    lines = ["# Dinner Date 玩家反馈结果", "",
             f"- 正式链路：`{result['pipeline']['path']}`",
             f"- 输入：{summary['snapshot_reviews']} 条原始 Review；固定分析 {summary['analyzed_reviews']} 条",
             f"- 输出：{summary['none_reviews']} 条无具体问题；{summary['observations']} 个 Observation；"
             f"{summary['issues']} 个 Issue", "",
             "## Issue 概览", "",
             "> 以下按出现 Review 数排序，仅表示本样本中的出现次数，不表示严重性。", "",
             "| Category | Issue | Review | Evidence |", "|---|---|---:|---|"]
    for issue in result["issues"]:
        example = issue["evidence"][0]
        evidence = example["evidence"].replace("\n", " ").replace("|", "\\|")
        lines.append(f"| `{issue['category']}` | {issue['title']} | {issue['review_count']} | "
                     f"`{example['review_id']}`：{evidence} |")
    lines += ["", "## 使用边界", ""] + [f"- {item}" for item in result["limitations"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Run the formal Dinner Date InsightPulse pipeline")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = build_product_result()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path = args.output_dir / f"dinner-date-product-{stamp}.json"
    report_path = args.output_dir / f"dinner-date-product-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **result["summary"],
                      "model_calls": 0, "tokens": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
