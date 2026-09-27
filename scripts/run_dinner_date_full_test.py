"""Run all 830 Dinner Date Reviews through v2.3 extraction and incremental Issue matching."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import load_env
import data_flow_v2 as flow
import pipeline
from scripts.run_dinner_date_pipeline import (SOURCE, OBSERVATION_CACHE, EMBEDDING_CACHE,
                                               build_product_result)
import vector_issue_experiment as vector

FOLDER = ROOT / "evaluation/dinner-date-full-run"
EXTRACTION_CACHE = FOLDER / "observations-v2.3-830.json"
FULL_EMBEDDINGS = FOLDER / "embeddings-minilm-v2.3-830.json"
ISSUE_CHECKPOINT = FOLDER / "issue-batches-rerank-v2.0.json"
COARSE_ISSUE_CHECKPOINT = FOLDER / f"issues-{flow.ISSUE_GROUP_VERSION}-830.json"
ISSUE_BATCH_SIZE = 30
GROUP_MINIMUM_SIMILARITY = 0.9


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _source_hash():
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest().upper()


def extract_all(dataset, model):
    source_hash = _source_hash()
    if EXTRACTION_CACHE.exists():
        state = _load(EXTRACTION_CACHE)
        if (state.get("source_sha256"), state.get("model"), state.get("prompt_version")) != (
                source_hash, model, flow.PROMPT_VERSION):
            raise ValueError("Full extraction checkpoint belongs to another source or model")
    else:
        seed = _load(OBSERVATION_CACHE)
        if seed.get("model") != model or seed.get("prompt_version") != flow.PROMPT_VERSION:
            raise ValueError("The verified 100-Review cache uses another model or prompt")
        state = {"source_sha256": source_hash, "model": model, "prompt_version": flow.PROMPT_VERSION,
                 "seeded_reviews": len(seed["records"]),
                 "records": {row["review_id"]: row for row in seed["records"]}, "api_calls": []}
        _save(EXTRACTION_CACHE, state)

    by_review = {row["review_id"]: row for row in dataset["review_versions"].values()}
    retrying = {rid for rid, row in state["records"].items() if row.get("failure")}
    pending = [rid for rid in sorted(by_review, key=pipeline.fingerprint)
               if rid not in state["records"] or rid in retrying]
    batch_size = 1 if retrying else 10
    for start in range(0, len(pending), batch_size):
        review_ids = pending[start:start + batch_size]
        version_ids = {by_review[rid]["review_version_id"] for rid in review_ids}
        calls = []
        outputs, failures, _ = flow.extract_versions(dataset, version_ids, model, audit=calls.append)
        state["api_calls"].extend(calls)
        for rid in review_ids:
            version = by_review[rid]
            extraction = outputs.get(version["review_version_id"], {})
            state["records"][rid] = {
                "review_id": rid, "text": version["text"], "language": version["language"],
                "recommended": version["recommended"], "categories": extraction.get("categories"),
                "observations": extraction.get("observations"),
                "failure": failures.get(version["review_version_id"]),
            }
        _save(EXTRACTION_CACHE, state)
        done = sum(not row.get("failure") for row in state["records"].values())
        print(f"extraction {done}/{len(by_review)}", flush=True)
        if any("HTTP 40" in str(call.get("error", "")) for call in calls):
            raise ValueError("Extraction stopped after an account or authorization error")
    failures = [row for row in state["records"].values() if row.get("failure")]
    if failures or set(state["records"]) != set(by_review):
        raise ValueError(f"Full extraction incomplete: {len(failures)} failed Reviews; rerun to retry")
    return state, [state["records"][rid] for rid in sorted(state["records"], key=pipeline.fingerprint)]


def load_embeddings(observations):
    expected_ids = {row["observation_id"] for row in observations}
    if FULL_EMBEDDINGS.exists():
        state = _load(FULL_EMBEDDINGS)
        if state.get("model") != vector.MULTILINGUAL_MODEL or not set(state.get("vectors", {})) <= expected_ids:
            raise ValueError("Full embedding checkpoint belongs to another dataset or model")
    else:
        seed = _load(EMBEDDING_CACHE)
        state = {"model": vector.MULTILINGUAL_MODEL, "dimensions": vector.MULTILINGUAL_DIMENSIONS,
                 "input_format": "category | object | symptom | description",
                 "vectors": {oid: values for oid, values in seed["vectors"].items() if oid in expected_ids}}
    missing = [row for row in observations if row["observation_id"] not in state["vectors"]]
    if missing:
        state["vectors"].update(vector.encode_multilingual(missing))
        _save(FULL_EMBEDDINGS, state)
    if set(state["vectors"]) != expected_ids or any(
            len(values) != vector.MULTILINGUAL_DIMENSIONS for values in state["vectors"].values()):
        raise ValueError("Full embedding coverage or dimensions are invalid")
    return state["vectors"]


def _base_issues():
    keys = ("issue_id", "category", "title", "canonical_description", "observation_ids", "review_ids")
    return [{key: issue[key] for key in keys} for issue in build_product_result()["issues"]]


def _reranked_group_inputs(chunk, observations, issues, embeddings):
    started = time.monotonic()
    recalled = vector.issue_candidates(chunk, issues, embeddings)
    reranked = vector.rerank_issue_candidates(recalled, observations, issues)
    original_pools = vector.category_pools_from_queries(chunk, reranked)
    groups = vector.consensus_observation_groups(
        chunk, reranked, embeddings, min_similarity=GROUP_MINIMUM_SIMILARITY)
    compressed, compressed_pools = vector.compressed_group_pools(groups, chunk, reranked)
    if {row["observation_id"] for row in compressed} & {row["observation_id"] for row in observations}:
        raise ValueError("Consensus group ID collided with an Observation ID")
    metrics = {"embedding_candidates": sum(len(row["candidates"]) for row in recalled),
               "reranked_candidates": sum(len(row["candidates"]) for row in reranked),
               "category_candidate_issues": sum(len(pool["candidate_issue_ids"])
                                                for pool in original_pools),
               "judgment_units": len(groups),
               "grouped_observations": sum(len(group["observation_ids"]) for group in groups
                                           if len(group["observation_ids"]) > 1),
               "rerank_seconds": round(time.monotonic() - started, 3)}
    return original_pools, groups, compressed, compressed_pools, metrics


def match_all(observations, embeddings, model):
    by_id = {row["observation_id"]: row for row in observations}
    issues = _base_issues()
    base_ids = {oid for issue in issues for oid in issue["observation_ids"]}
    new_ids = sorted(set(by_id) - base_ids)
    if ISSUE_CHECKPOINT.exists():
        state = _load(ISSUE_CHECKPOINT)
        if (state.get("source_sha256"), state.get("model"), state.get("batch_size"),
                state.get("observation_ids"), state.get("rerank_model"),
                state.get("rerank_top_k"), state.get("group_minimum_similarity")) != (
                _source_hash(), model, ISSUE_BATCH_SIZE, sorted(by_id), vector.RERANK_MODEL,
                vector.RERANK_TOP_K, GROUP_MINIMUM_SIMILARITY):
            raise ValueError("Issue checkpoint belongs to another full Observation set")
    else:
        state = {"source_sha256": _source_hash(), "model": model, "batch_size": ISSUE_BATCH_SIZE,
                 "observation_ids": sorted(by_id), "rerank_model": vector.RERANK_MODEL,
                 "rerank_top_k": vector.RERANK_TOP_K,
                 "group_minimum_similarity": GROUP_MINIMUM_SIMILARITY,
                 "batches": [], "api_calls": []}

    completed = 0
    for saved in state["batches"]:
        expected_ids = new_ids[completed:completed + ISSUE_BATCH_SIZE]
        if saved.get("observation_ids") != expected_ids:
            raise ValueError("Issue checkpoint batch order changed")
        chunk = [by_id[oid] for oid in expected_ids]
        original_pools, groups, compressed, compressed_pools, metrics = _reranked_group_inputs(
            chunk, observations, issues, embeddings)
        saved["metrics"] = metrics
        grouped = vector.validate_batch_reconciliation(
            json.dumps(saved["group_reconciliation"], ensure_ascii=False), compressed_pools, issues)
        reconciliation = vector.expand_group_reconciliation(grouped, groups, original_pools, issues)
        covered = {oid for issue in issues for oid in issue["observation_ids"]}
        issues = vector.apply_batch_reconciliation(
            issues, [by_id[oid] for oid in sorted(covered | set(expected_ids))], reconciliation)
        completed += len(expected_ids)

    while completed < len(new_ids):
        ids = new_ids[completed:completed + ISSUE_BATCH_SIZE]
        chunk = [by_id[oid] for oid in ids]
        original_pools, groups, compressed, compressed_pools, metrics = _reranked_group_inputs(
            chunk, observations, issues, embeddings)
        calls = []
        try:
            grouped = vector.reconcile_category_pools(
                compressed_pools, observations + compressed, issues, calls.append)
        except ValueError:
            state["api_calls"].extend(calls)
            state.setdefault("failed_batches", []).append({"observation_ids": ids, "api_calls": calls})
            _save(ISSUE_CHECKPOINT, state)
            raise
        reconciliation = vector.expand_group_reconciliation(grouped, groups, original_pools, issues)
        covered = {oid for issue in issues for oid in issue["observation_ids"]}
        issues = vector.apply_batch_reconciliation(
            issues, [by_id[oid] for oid in sorted(covered | set(ids))], reconciliation)
        state["batches"].append({"observation_ids": ids, "groups": groups,
                                 "category_pools": compressed_pools,
                                 "group_reconciliation": grouped,
                                 "reconciliation": reconciliation, "metrics": metrics})
        state["api_calls"].extend(calls)
        _save(ISSUE_CHECKPOINT, state)
        completed += len(ids)
        print(f"issue matching {completed}/{len(new_ids)}", flush=True)
    _save(ISSUE_CHECKPOINT, state)
    return state, issues, len(new_ids)


def group_all(observations, model):
    observation_ids = sorted(row["observation_id"] for row in observations)
    contract = (_source_hash(), model, flow.ISSUE_GROUP_VERSION,
                pipeline.fingerprint([flow.ISSUE_GROUP_PROMPT, flow.ISSUE_CONSOLIDATION_PROMPT,
                                      flow.ISSUE_MEMBERSHIP_PROMPT, flow.ISSUE_BOUNDARY_PROMPT]),
                flow.ISSUE_GROUP_BATCH_SIZE, observation_ids)
    if COARSE_ISSUE_CHECKPOINT.exists():
        state = _load(COARSE_ISSUE_CHECKPOINT)
        actual = (state.get("source_sha256"), state.get("model"), state.get("issue_group_version"),
                  state.get("prompt_hash"), state.get("batch_size"), state.get("observation_ids"))
        if actual != contract:
            raise ValueError("Issue grouping checkpoint belongs to another contract")
    else:
        state = {"source_sha256": contract[0], "model": model, "issue_group_version": contract[2],
                 "prompt_hash": contract[3], "batch_size": contract[4], "observation_ids": observation_ids,
                 "category_issues": {}, "api_calls": []}
    issues = []
    for category in sorted({row["category"] for row in observations}):
        members = [row for row in observations if row["category"] == category]
        if category not in state["category_issues"]:
            calls = []
            try:
                state["category_issues"][category] = flow.group_observations(
                    members, calls.append, app_id=flow.APP_ID, model=model)
            finally:
                state["api_calls"].extend(calls)
                _save(COARSE_ISSUE_CHECKPOINT, state)
            print(f"issue grouping {category}: {len(members)} observations → "
                  f"{len(state['category_issues'][category])} issues", flush=True)
        content = json.dumps({"issues": state["category_issues"][category]}, ensure_ascii=False)
        issues.extend(flow.validate_issue_groups(content, members, flow.APP_ID))
    if "membership_issues" not in state:
        calls = []
        try:
            state["membership_issues"] = flow.audit_large_issue_membership(
                observations, issues, calls.append, app_id=flow.APP_ID, model=model)
        finally:
            state["api_calls"].extend(calls)
            _save(COARSE_ISSUE_CHECKPOINT, state)
    issues = flow.validate_issue_groups(
        json.dumps({"issues": state["membership_issues"]}, ensure_ascii=False), observations, flow.APP_ID)
    if state.get("boundary_version") != flow.ISSUE_BOUNDARY_VERSION:
        calls = []
        try:
            state["audited_issues"] = flow.audit_issue_boundaries(
                observations, issues, calls.append, app_id=flow.APP_ID, model=model)
            state["boundary_version"] = flow.ISSUE_BOUNDARY_VERSION
        finally:
            state["api_calls"].extend(calls)
            _save(COARSE_ISSUE_CHECKPOINT, state)
    issues = flow.validate_issue_groups(
        json.dumps({"issues": state["audited_issues"]}, ensure_ascii=False), observations, flow.APP_ID)
    flow.issue_quality_diagnostics(observations, issues)
    return state, sorted(issues, key=lambda row: (row["category"], row["issue_id"]))


def main():
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    issue_model = os.environ.get("DEEPSEEK_ISSUE_MODEL", "deepseek-v4-pro")
    snapshot = _load(SOURCE)
    dataset = flow.load_snapshots([snapshot])
    extraction_state, records = extract_all(dataset, model)
    observations = vector.observations_from_records(records, dataset)
    issue_state, issues = group_all(observations, issue_model)

    token_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    extraction_usage = Counter({field: sum(call.get("usage", {}).get(field, 0)
                                            for call in extraction_state["api_calls"])
                                for field in token_fields})
    issue_usage = Counter({field: sum(call.get("usage", {}).get(field, 0)
                                      for call in issue_state["api_calls"])
                           for field in token_fields})
    category_counts = dict(sorted(Counter(row["category"] for row in observations).items()))
    category_issue_counts = dict(sorted(Counter(row["category"] for row in issues).items()))
    none_count = sum(len(row["categories"]) == 1 and row["categories"][0]["category"] == "none"
                     for row in records)
    quality = flow.issue_quality_diagnostics(observations, issues)
    summary = {"reviews": len(records), "failed_reviews": 0, "none_reviews": none_count,
               "observations": len(observations), "categories": len(category_counts),
               "final_issues": len(issues),
               "singleton_issues": sum(len(issue["observation_ids"]) == 1 for issue in issues),
               "singleton_issue_share": quality["single_observation_issue_share"],
               "extraction_calls": len(extraction_state["api_calls"]),
               "extraction_failed_calls": sum(call.get("status") == "failed"
                                                for call in extraction_state["api_calls"]),
               "issue_calls": len(issue_state["api_calls"]),
               "prompt_tokens": extraction_usage["prompt_tokens"] + issue_usage["prompt_tokens"],
               "completion_tokens": extraction_usage["completion_tokens"] + issue_usage["completion_tokens"],
               "total_tokens": extraction_usage["total_tokens"] + issue_usage["total_tokens"]}
    result = {"purpose": "Full Dinner Date 830-Review pipeline test",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "source": {"file": str(SOURCE), "sha256": _source_hash()},
              "processing_version": flow.PROCESSING_VERSION, "prompt_version": flow.PROMPT_VERSION,
              "issue_group": {"version": flow.ISSUE_GROUP_VERSION,
                              "prompt_hash": pipeline.fingerprint([flow.ISSUE_GROUP_PROMPT,
                                                                   flow.ISSUE_CONSOLIDATION_PROMPT,
                                                                   flow.ISSUE_MEMBERSHIP_PROMPT,
                                                                   flow.ISSUE_BOUNDARY_PROMPT]),
                              "batch_size": flow.ISSUE_GROUP_BATCH_SIZE,
                              "boundary_version": flow.ISSUE_BOUNDARY_VERSION,
                              "mode": "holistic_within_category", "model": issue_model},
              "summary": summary, "category_observation_counts": category_counts,
              "category_issue_counts": category_issue_counts,
              "issue_quality": quality,
              "token_breakdown": {"observation_extraction": dict(extraction_usage),
                                  "issue_reconciliation": dict(issue_usage)},
              "issues": issues, "observations": observations,
              "api_calls": {"extraction": extraction_state["api_calls"],
                            "issue_reconciliation": issue_state["api_calls"]}}
    if "severity" in json.dumps(result, ensure_ascii=False):
        raise ValueError("severity is forbidden")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, report_path = FOLDER / f"run-{stamp}.json", FOLDER / f"run-{stamp}.md"
    _save(json_path, result)
    narrative = [issue for issue in issues if issue["category"] == "content_narrative"]
    controls = [issue for issue in issues if issue["category"] == "controls_input"]
    lines = ["# Dinner Date 全量主链路测试", "",
             f"- {summary['reviews']} Review / {summary['none_reviews']} none / "
             f"{summary['observations']} Observation / {summary['final_issues']} Issue",
              f"- 新增调用：抽取 {summary['extraction_calls']} 次；Issue reconciliation "
              f"{summary['issue_calls']} 次；Token {summary['total_tokens']}",
              f"- Issue 生成：类别内整体归并；版本 `{flow.ISSUE_GROUP_VERSION}`",
             f"- Issue：{summary['final_issues']} 个，其中 {summary['singleton_issues']} 个仅含1条 Observation", "",
             "## Category Observation → Issue", "",
             "| Category | Observation | Issue | Obs / Issue | Singleton | 告警 |",
             "|---|---:|---:|---:|---:|---|"]
    lines += [f"| `{row['category']}` | {row['observations']} | {row['issues']} | "
              f"{row['observations_per_issue']} | {row['single_observation_issues']} "
              f"({row['single_observation_issue_share']:.1%}) | "
              f"{'碎片化' if row['fragmentation_warning'] else ''} |" for row in quality["categories"]]
    lines += ["", "## 全量观察", "",
              f"- `content_narrative`：{category_counts.get('content_narrative', 0)} Observation → "
              f"{len(narrative)} Issue，其中"
              f"{sum(len(issue['observation_ids']) == 1 for issue in narrative)}个单条。",
              f"- `controls_input`：最大 Issue 含"
              f"{max((len(issue['observation_ids']) for issue in controls), default=0)} Observation。",
              "- 本轮没有修改 Category 或 Observation prompt。"]
    lines += ["", "## Issue（按 Review 数排序）", "", "| Category | Issue | Review |", "|---|---|---:|"]
    for issue in sorted(issues, key=lambda row: (-len(row["review_ids"]), row["category"], row["issue_id"])):
        lines.append(f"| `{issue['category']}` | {issue['title']} | {len(issue['review_ids'])} |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
