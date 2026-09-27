"""Run the Dinner Date v2.3 vector-candidate Issue experiment."""
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import load_env
import data_flow_v2 as flow
import pipeline
from scripts.group_dinner_date_observations import (BASELINE, BASELINE_SHA256, SOURCE,
                                                    load_frozen_observations)
import vector_issue_experiment as vector

SAMPLE_SIZE = 100
CACHE = ROOT / "evaluation/dinner-date-vector-issue/observations-v2.3-100.json"


observations_from_records = vector.observations_from_records


def expanded_observations(model):
    _, frozen = load_frozen_observations()
    snapshot = json.loads(SOURCE.read_text(encoding="utf-8"))
    dataset = flow.load_snapshots([snapshot])
    if CACHE.exists():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if (cached.get("baseline_sha256"), cached.get("prompt_version"), cached.get("model"),
                cached.get("review_count")) != (BASELINE_SHA256, flow.PROMPT_VERSION, model, SAMPLE_SIZE):
            raise ValueError("Expanded Observation cache belongs to another baseline or model")
        return dataset, cached["records"], cached["extraction_api_calls"], cached.get("reused_reviews", 50), frozen

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    by_review = {row["review_id"]: row for row in dataset["review_versions"].values()}
    selected = {row["review_id"] for row in baseline["reviews"]}
    for recommended in (True, False):
        target = SAMPLE_SIZE // 2
        existing = sum(by_review[rid]["recommended"] == recommended for rid in selected)
        candidates = (rid for rid, row in by_review.items()
                      if row["recommended"] == recommended and rid not in selected)
        selected.update(sorted(candidates, key=pipeline.fingerprint)[:target - existing])
    if len(selected) != SAMPLE_SIZE:
        raise ValueError("Dinner Date source cannot supply the requested balanced sample")

    extraction_cache = {}
    for record in baseline["reviews"]:
        version = by_review[record["review_id"]]
        key = pipeline.fingerprint([flow.PROMPT_VERSION, flow.OBSERVATION_PROMPT, model,
                                    version["review_id"], version["text"]])
        extraction_cache[key] = {"categories": record["categories"], "observations": record["observations"]}
    version_ids = {by_review[rid]["review_version_id"] for rid in selected}
    calls = []
    outputs, failures, _ = flow.extract_versions(dataset, version_ids, model, extraction_cache, calls.append)
    records = []
    for rid in sorted(selected, key=pipeline.fingerprint):
        version = by_review[rid]
        extraction = outputs.get(version["review_version_id"], {})
        records.append({"review_id": rid, "text": version["text"], "language": version["language"],
                        "recommended": version["recommended"], "categories": extraction.get("categories"),
                        "observations": extraction.get("observations"),
                        "failure": failures.get(version["review_version_id"])})
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                                 "baseline_sha256": BASELINE_SHA256,
                                 "prompt_version": flow.PROMPT_VERSION, "model": model,
                                 "review_count": SAMPLE_SIZE, "reused_reviews": len(baseline["reviews"]),
                                 "records": records,
                                 "extraction_api_calls": calls}, ensure_ascii=False, indent=2), encoding="utf-8")
    return dataset, records, calls, len(baseline["reviews"]), frozen


def example_payload(decision, by_id, issues):
    left, right = by_id[decision["left_observation_id"]], by_id[decision["right_observation_id"]]
    left_issue = next(issue for issue in issues if left["observation_id"] in issue["observation_ids"])
    right_issue = next(issue for issue in issues if right["observation_id"] in issue["observation_ids"])
    return {"similarity": decision["similarity"], "llm_same_issue": decision["same_issue"],
            "llm_reason": decision["reason"],
            "left": {key: left[key] for key in ("observation_id", "review_id", "object", "symptom",
                                                 "description", "evidence")},
            "right": {key: right[key] for key in ("observation_id", "review_id", "object", "symptom",
                                                   "description", "evidence")},
            "final_issue": left_issue if decision["same_issue"] else None,
            "final_separate_issues": None if decision["same_issue"] else
            {"left": left_issue, "right": right_issue}}


def main():
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    dataset, records, extraction_calls, reused_reviews, frozen = expanded_observations(model)
    observations = observations_from_records(records, dataset)
    by_id = {row["observation_id"]: row for row in observations}

    embeddings = {oid: vector.encode_observation(row) for oid, row in by_id.items()}
    retrieval = vector.topk_neighbors(observations, embeddings)
    pairs = vector.candidate_pairs(observations, retrieval)

    prior_path = max(CACHE.parent.glob("run-*.json"), default=None)
    prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path else {}
    prior_model = next((call.get("model") for call in prior.get("api_calls", {}).get("pure_llm_grouping", [])), None)
    same_observations = {row["observation_id"] for row in prior.get("observations", [])} == set(by_id)
    if prior_model == model and same_observations and prior.get("pure_llm_issues"):
        pure_issues = prior["pure_llm_issues"]
        pure_calls = prior["api_calls"]["pure_llm_grouping"]
    else:
        pure_calls = []
        pure_issues = flow.group_observations(observations, pure_calls.append)
    pair_prompt_hash = pipeline.fingerprint(vector.PAIR_PROMPT)
    same_pairs = {row["pair_id"] for row in prior.get("candidate_pairs", [])} == {row["pair_id"] for row in pairs}
    if prior.get("vector_pair_prompt_hash") == pair_prompt_hash and same_pairs:
        decisions = prior["pair_decisions"]
        vector_calls = prior["api_calls"]["vector_candidate_judgment"]
    else:
        vector_calls = []
        decisions = vector.judge_pairs(pairs, vector_calls.append)
    conflicts = []
    vector_issues = vector.form_issues(observations, decisions, conflicts=conflicts)

    accepted = [row for row in decisions if row["same_issue"]]
    rejected = [row for row in decisions if not row["same_issue"]]
    hand_merge = next((row for row in sorted(accepted, key=lambda item: -item["similarity"])
                       if "hand" in (by_id[row["left_observation_id"]]["object"]
                                     + by_id[row["right_observation_id"]]["object"])), None)
    rejection = next((row for row in sorted(rejected, key=lambda item: -item["similarity"])
                      if {by_id[row["left_observation_id"]]["object"],
                          by_id[row["right_observation_id"]]["object"]} & {"choices", "monologue"}),
                     max(rejected, key=lambda item: item["similarity"], default=None))
    incident_yes = {oid for row in accepted for oid in (row["left_observation_id"], row["right_observation_id"])}
    retrieval_by_id = {row["observation_id"]: row["neighbors"] for row in retrieval}
    singleton = next((issue for issue in vector_issues if len(issue["observation_ids"]) == 1
                      and issue["observation_ids"][0] not in incident_yes
                      and retrieval_by_id[issue["observation_ids"][0]]), None)
    if singleton is None:
        singleton = next((issue for issue in vector_issues if len(issue["observation_ids"]) == 1), None)

    pure_by_observation = {oid: issue["issue_id"] for issue in pure_issues for oid in issue["observation_ids"]}
    vector_by_observation = {oid: issue["issue_id"] for issue in vector_issues for oid in issue["observation_ids"]}
    same_category_pairs = [(left, right) for left, right in combinations(by_id, 2)
                           if by_id[left]["category"] == by_id[right]["category"]]
    recalled_pairs = {frozenset((pair["left"]["observation_id"], pair["right"]["observation_id"]))
                      for pair in pairs}
    pure_same_pairs = [(left, right) for left, right in same_category_pairs
                       if pure_by_observation[left] == pure_by_observation[right]]
    method_pair_comparison = {
        "same_category_pairs": len(same_category_pairs),
        "pure_llm_same_issue_pairs": len(pure_same_pairs),
        "topk_recalled_pure_llm_same_pairs": sum(frozenset(pair) in recalled_pairs for pair in pure_same_pairs),
        "pure_llm_same_but_vector_split": sum(vector_by_observation[left] != vector_by_observation[right]
                                                for left, right in pure_same_pairs),
        "pure_llm_split_but_vector_merged": sum(pure_by_observation[left] != pure_by_observation[right]
                                                and vector_by_observation[left] == vector_by_observation[right]
                                                for left, right in same_category_pairs),
    }

    category_counts = dict(sorted(Counter(row["category"] for row in observations).items()))
    none_count = sum(record.get("categories") and record["categories"][0]["category"] == "none"
                     for record in records if len(record.get("categories") or []) == 1)
    successful_reviews = sum(not row.get("failure") for row in records)
    summary = {"reviews": len(records), "successful_reviews": successful_reviews,
               "failed_reviews": len(records) - successful_reviews, "reused_reviews": reused_reviews,
               "observations": len(observations), "none_reviews": none_count,
               "categories_with_observations": len(category_counts), "candidate_pairs": len(pairs),
               "accepted_pairs": len(accepted), "rejected_pairs": len(rejected),
               "blocked_inconsistent_positive_edges": len(conflicts),
               "pure_llm_issues": len(pure_issues), "vector_candidate_issues": len(vector_issues),
               "cross_category_merges": 0,
               "extraction_calls": len(extraction_calls), "pure_group_calls": len(pure_calls),
               "vector_judge_calls": len(vector_calls),
               "extraction_tokens": sum(c.get("usage", {}).get("total_tokens", 0) for c in extraction_calls),
               "pure_group_prompt_tokens": sum(c.get("usage", {}).get("prompt_tokens", 0) for c in pure_calls),
               "pure_group_total_tokens": sum(c.get("usage", {}).get("total_tokens", 0) for c in pure_calls),
               "vector_judge_prompt_tokens": sum(c.get("usage", {}).get("prompt_tokens", 0) for c in vector_calls),
               "vector_judge_total_tokens": sum(c.get("usage", {}).get("total_tokens", 0) for c in vector_calls)}

    new_issue_example = None
    if singleton:
        oid = singleton["observation_ids"][0]
        new_issue_example = {"observation": by_id[oid],
                             "top_k": retrieval_by_id[oid],
                             "final_issue": singleton}
    result = {"purpose": "Category-local vector Top-K recall followed by LLM same-Issue judgment",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "frozen_observation_baseline": {"file": str(BASELINE), "sha256": BASELINE_SHA256,
                                              "reviews": 50, "observations": len(frozen),
                                              "pure_llm_issues": 17},
              "processing_version": flow.PROCESSING_VERSION, "prompt_version": flow.PROMPT_VERSION,
              "pure_issue_group_version": flow.ISSUE_GROUP_VERSION,
              "vector_pair_prompt_hash": pair_prompt_hash,
              "embedding": {"model": vector.EMBEDDING_MODEL, "dimensions": vector.EMBEDDING_DIMENSIONS,
                            "input_format": "category | object | symptom | description", "cost": 0,
                            "vectors": [{"observation_id": oid,
                                         "text": vector.embedding_text(by_id[oid]),
                                         "values": [[index, round(value, 8)] for index, value in sorted(values.items())]}
                                        for oid, values in sorted(embeddings.items())]},
              "retrieval": {"top_k": vector.TOP_K, "similarity": "cosine",
                            "threshold": None, "threshold_used_for_merge": False,
                            "neighbors": retrieval},
              "summary": summary, "category_observation_counts": category_counts,
              "method_pair_comparison": method_pair_comparison,
              "candidate_pairs": pairs, "pair_decisions": decisions,
              "local_conflict_blocks": conflicts,
              "pure_llm_issues": pure_issues, "vector_candidate_issues": vector_issues,
              "observations": observations,
              "examples": {"correct_recall_and_merge": example_payload(hand_merge, by_id, vector_issues)
                           if hand_merge else None,
                           "similar_but_rejected": example_payload(rejection, by_id, vector_issues)
                           if rejection else None,
                           "new_issue": new_issue_example},
              "api_calls": {"observation_extraction": extraction_calls,
                            "pure_llm_grouping": pure_calls, "vector_candidate_judgment": vector_calls}}

    folder = CACHE.parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, report_path = folder / f"run-{stamp}.json", folder / f"run-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def describe(example):
        if not example:
            return ["未找到对应样本。"]
        rows = [f"- A `{example['left']['review_id']}`：{example['left']['description']} — {example['left']['evidence']}",
                f"- B `{example['right']['review_id']}`：{example['right']['description']} — {example['right']['evidence']}",
                f"- cosine：`{example['similarity']}`；LLM：`{example['llm_same_issue']}`；{example['llm_reason']}"]
        if example["final_issue"]:
            rows.append(f"- 最终：合并为 `{example['final_issue']['title']}`")
        else:
            separate = example["final_separate_issues"]
            rows.append(f"- 最终：分别保留为 `{separate['left']['title']}` / `{separate['right']['title']}`")
        return rows

    lines = ["# Dinner Date Category 内向量候选召回实验", "",
             "## 结果摘要", "", "| 项目 | 当前纯 LLM | Top-K → LLM 裁判 |", "|---|---:|---:|",
             f"| Review | {summary['reviews']} | {summary['reviews']} |",
             f"| Observation | {summary['observations']} | {summary['observations']} |",
             f"| Issue | {summary['pure_llm_issues']} | {summary['vector_candidate_issues']} |",
             f"| LLM 调用 | {summary['pure_group_calls']} | {summary['vector_judge_calls']} |",
             f"| LLM 输入 Token | {summary['pure_group_prompt_tokens']} | {summary['vector_judge_prompt_tokens']} |",
             f"| LLM 总 Token | {summary['pure_group_total_tokens']} | {summary['vector_judge_total_tokens']} |",
             "| 跨 Category 合并 | 0 | 0 |", "",
             f"- 冻结基线：50 Review / 32 Observation / 17 Issue；SHA-256 `{BASELINE_SHA256}`",
             f"- 扩大样本：{summary['reviews']} Review；{summary['observations']} Observation；"
             f"none {summary['none_reviews']} Review；失败 {summary['failed_reviews']} Review",
             f"- Embedding：`{vector.EMBEDDING_MODEL}`，输入 `category | object | symptom | description`",
             f"- 检索：同 Category Top-{vector.TOP_K}；无 similarity threshold；相似度不参与自动合并", "",
             f"- 与纯 LLM 的 pair 对照：其同组 pair 共 {method_pair_comparison['pure_llm_same_issue_pairs']}；"
             f"Top-K 召回 {method_pair_comparison['topk_recalled_pure_llm_same_pairs']}；"
             f"向量方案拆开 {method_pair_comparison['pure_llm_same_but_vector_split']}；"
             f"向量方案额外合并 {method_pair_comparison['pure_llm_split_but_vector_merged']}", "",
             "## Category Observation 数量", "", "| Category | Observation |", "|---|---:|"]
    lines += [f"| {category} | {count} |" for category, count in category_counts.items()]
    lines += ["", "## 正确召回并合并", ""] + describe(result["examples"]["correct_recall_and_merge"])
    lines += ["", "## 相似但拒绝合并", ""] + describe(result["examples"]["similar_but_rejected"])
    lines += ["", "## 新 Issue", ""]
    if new_issue_example:
        row = new_issue_example["observation"]
        lines += [f"- `{row['review_id']}`：{row['description']} — {row['evidence']}",
                  f"- Top-K：`{json.dumps(new_issue_example['top_k'], ensure_ascii=False)}`",
                  f"- 最终：新建 `{singleton['title']}`（`{singleton['issue_id']}`）"]
    lines += ["", "## Top-K → LLM 最终 Issue", "", "| Category | Issue | Observation | Review |",
              "|---|---|---:|---|"]
    for issue in vector_issues:
        lines.append(f"| {issue['category']} | {issue['title']} | {len(issue['observation_ids'])} | "
                     f"{', '.join(issue['review_ids'])} |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
