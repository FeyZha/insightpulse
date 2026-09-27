"""Run a 50-review Dinner Date Category/Observation inventory."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import load_env
import data_flow_v2 as flow
import pipeline

SOURCE = ROOT / "data/steam-94000-current-20260911.json"
SAMPLE_SIZE = 50
ANCHORS = ("2298516", "13950693", "9461342", "811384", "1847211", "7881838",
           "25197093", "847629", "288934")


def main():
    snapshot = json.loads(SOURCE.read_text(encoding="utf-8"))
    dataset = flow.load_snapshots([snapshot])
    versions = dataset["review_versions"]
    by_review = {version["review_id"]: version for version in versions.values()}
    selected = set(ANCHORS)
    for recommended in (True, False):
        target = SAMPLE_SIZE // 2
        existing = sum(by_review[rid]["recommended"] == recommended for rid in selected)
        candidates = (rid for rid, row in by_review.items()
                      if row["recommended"] == recommended and rid not in selected)
        selected.update(sorted(candidates, key=pipeline.fingerprint)[:target - existing])
    assert len(selected) == SAMPLE_SIZE and selected.issubset(by_review)

    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    prior_path = max((ROOT / "evaluation/dinner-date-observation-test").glob("run-*.json"), default=None)
    cache = {}
    if prior_path:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        if prior.get("prompt_version") == flow.PROMPT_VERSION and prior.get("model") == model:
            prior_rows = [pair["current"] for pair in prior.get("pairs", [])] + prior.get("additional_reviews", [])
            for row in prior_rows:
                vid = row["version_id"]
                if vid in versions and row.get("categories") is not None and row.get("observations") is not None:
                    version = versions[vid]
                    key = pipeline.fingerprint([flow.PROMPT_VERSION, flow.OBSERVATION_PROMPT, model,
                                                version["review_id"], version["text"]])
                    cache[key] = {"categories": row["categories"], "observations": row["observations"]}

    version_ids = {by_review[rid]["review_version_id"] for rid in selected}
    reused = sum(pipeline.fingerprint([flow.PROMPT_VERSION, flow.OBSERVATION_PROMPT, model,
                                      versions[vid]["review_id"], versions[vid]["text"]]) in cache
                 for vid in version_ids)
    calls = []
    outputs, failures, _ = flow.extract_versions(dataset, version_ids, model, cache, calls.append)
    records = []
    for rid in sorted(selected, key=pipeline.fingerprint):
        version = by_review[rid]
        extraction = outputs.get(version["review_version_id"], {})
        records.append({"review_id": rid, "text": version["text"], "language": version["language"],
                        "recommended": version["recommended"], "categories": extraction.get("categories"),
                        "observations": extraction.get("observations"),
                        "failure": failures.get(version["review_version_id"])})

    groups = defaultdict(lambda: {"review_ids": set(), "observations": 0, "confidences": [], "examples": []})
    for record in records:
        if record["failure"]:
            continue
        confidences = {item["category"]: item["confidence"] for item in record["categories"]}
        if not record["observations"]:
            group = groups[("none", "", "")]
            group["review_ids"].add(record["review_id"])
            group["confidences"].append(confidences["none"])
            group["examples"].append({"review_id": record["review_id"], "evidence": record["text"]})
        for observation in record["observations"]:
            key = (observation["category"], observation["object"], observation["symptom"])
            group = groups[key]
            group["review_ids"].add(record["review_id"])
            group["observations"] += 1
            group["confidences"].append(confidences[observation["category"]])
            group["examples"].append({"review_id": record["review_id"], "description": observation["description"],
                                      "evidence": observation["evidence"]})
    inventory = []
    for (category, object_, symptom), group in groups.items():
        inventory.append({"category": category, "object": object_, "symptom": symptom,
                          "review_count": len(group["review_ids"]), "observation_count": group["observations"],
                          "average_confidence": round(sum(group["confidences"]) / len(group["confidences"]), 3),
                          "examples": group["examples"][:2]})
    inventory.sort(key=lambda row: (row["category"], -row["review_count"], row["object"], row["symptom"]))
    categories = Counter(item["category"] for record in records if record["categories"]
                         for item in record["categories"])
    issue_rows = [row for row in inventory if row["category"] != "none"]
    summary = {"selected_reviews": len(records), "successful_reviews": len(outputs),
               "failed_reviews": len(failures), "reused_reviews": reused,
               "api_calls": len(calls), "retry_calls": sum(bool(call.get("retry_review_ids")) for call in calls),
               "total_tokens": sum(call.get("usage", {}).get("total_tokens", 0) for call in calls),
               "observations": sum(len(record["observations"] or []) for record in records),
               "natural_issue_keys": len(issue_rows),
               "singleton_issue_keys": sum(row["review_count"] == 1 for row in issue_rows),
               "none_reviews": categories["none"], "other_reviews": categories["other"],
               "multi_category_reviews": sum(len(record["categories"] or []) > 1 for record in records)}
    result = {"purpose": "Inventory natural Observation keys before semantic Issue grouping",
              "created_at": datetime.now(timezone.utc).isoformat(), "processing_version": flow.PROCESSING_VERSION,
              "prompt_version": flow.PROMPT_VERSION, "model": model, "source_file": str(SOURCE),
              "sampling": "50 current reviews: nine anchors, then deterministic hash sampling balanced 25/25 by recommendation",
              "category_review_counts": dict(sorted(categories.items())), "summary": summary,
              "inventory": inventory, "reviews": records, "api_calls": calls}

    folder = ROOT / "evaluation/dinner-date-observation-audit"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, report_path = folder / f"run-{stamp}.json", folder / f"run-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def cell(value, limit=180):
        value = str(value).replace("\n", " ").replace("|", "\\|")
        return value if len(value) <= limit else value[:limit - 1] + "…"

    lines = ["# Dinner Date Observation 盘点", "", f"- 样本：{summary['selected_reviews']} 条，成功 {summary['successful_reviews']} 条",
             f"- Observation：{summary['observations']} 条，自然 Issue Key：{summary['natural_issue_keys']} 个，其中单次 Key：{summary['singleton_issue_keys']} 个",
             f"- none：{summary['none_reviews']} 条，多 Category：{summary['multi_category_reviews']} 条",
             "", "## Category 分布", "", "| Category | Review 数 |", "|---|---:|"]
    lines += [f"| {category} | {count} |" for category, count in sorted(categories.items())]
    lines += ["", "## Category → object/symptom 盘点", "",
              "| Category | Object | Symptom | Review 数 | 平均置信度 | 示例原文 |",
              "|---|---|---|---:|---:|---|"]
    for row in inventory:
        examples = "<br>".join(f"`{item['review_id']}` {cell(item['evidence'])}" for item in row["examples"])
        lines.append(f"| {row['category']} | {row['object'] or '-'} | {row['symptom'] or '-'} | "
                     f"{row['review_count']} | {row['average_confidence']:.3f} | {examples} |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
