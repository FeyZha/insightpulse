"""Semantically group the frozen Dinner Date v2.3 Observation baseline once."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import load_env
import data_flow_v2 as flow
import pipeline

BASELINE = ROOT / "evaluation/dinner-date-observation-audit/run-20260912T153425965550Z.json"
BASELINE_SHA256 = "A0BF3A427516173F1B271015BD5E1D87B1ADEDDD5333ECE004560AC042C5B29E"
SOURCE = ROOT / "data/steam-94000-current-20260911.json"


def load_frozen_observations():
    raw = BASELINE.read_bytes()
    if hashlib.sha256(raw).hexdigest().upper() != BASELINE_SHA256:
        raise ValueError("Frozen v2.3 baseline hash changed")
    baseline = json.loads(raw)
    observations_count = sum(len(row.get("observations") or []) for row in baseline.get("reviews", []))
    none_count = sum(len(row.get("categories") or []) == 1 and row["categories"][0]["category"] == "none"
                     for row in baseline.get("reviews", []))
    if (baseline.get("processing_version"), baseline.get("prompt_version"),
            len(baseline.get("reviews", [])), observations_count, none_count) != (
            "processing-v2.3", "observation-v2.3", 50, 32, 29):
        raise ValueError("Frozen v2.3 baseline contract changed")

    dataset = flow.load_snapshots([json.loads(SOURCE.read_text(encoding="utf-8"))])
    by_review = {row["review_id"]: row for row in dataset["review_versions"].values()}
    observations = []
    for record in baseline["reviews"]:
        if record.get("failure"):
            raise ValueError("Frozen baseline contains a failed Review")
        version = by_review.get(record["review_id"])
        if not version or version["text"] != record["text"]:
            raise ValueError("Frozen Review no longer matches the Dinner Date source")
        extraction = flow.validate_observations(
            json.dumps({"reviews": [{"id": version["review_version_id"],
                                     "categories": record["categories"],
                                     "observations": record["observations"]}]}, ensure_ascii=False),
            [{"id": version["review_version_id"], "text": version["text"]}],
        )[version["review_version_id"]]
        if extraction != {"categories": record["categories"], "observations": record["observations"]}:
            raise ValueError("Frozen Observation normalization changed")
        for item in extraction["observations"]:
            observations.append(dict(
                item,
                observation_id=pipeline.fingerprint([version["review_version_id"], item])[:24],
                review_id=record["review_id"],
                review_version_id=version["review_version_id"],
                review_text=version["text"],
            ))
    if len({row["observation_id"] for row in observations}) != 32:
        raise ValueError("Frozen baseline does not contain 32 unique Observations")
    return baseline, sorted(observations, key=lambda row: (row["category"], row["observation_id"]))


def main():
    baseline, observations = load_frozen_observations()
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    calls = []
    issues = flow.group_observations(observations, calls.append)
    by_observation = {row["observation_id"]: row for row in observations}
    sizes = Counter(len(issue["observation_ids"]) for issue in issues)
    summary = {
        "input_reviews": 50,
        "input_observations": len(observations),
        "issue_count": len(issues),
        "merged_issue_count": sum(count for size, count in sizes.items() if size > 1),
        "singleton_issue_count": sizes[1],
        "api_calls": len(calls),
        "total_tokens": sum(call.get("usage", {}).get("total_tokens", 0) for call in calls),
    }
    result = {
        "purpose": "Human review of semantic Observation-to-Issue grouping",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {"file": str(BASELINE), "sha256": BASELINE_SHA256,
                     "processing_version": baseline["processing_version"],
                     "prompt_version": baseline["prompt_version"]},
        "issue_group_version": flow.ISSUE_GROUP_VERSION,
        "issue_group_prompt_hash": pipeline.fingerprint(flow.ISSUE_GROUP_PROMPT),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "summary": summary,
        "issues": issues,
        "observations": observations,
        "api_calls": calls,
    }

    folder = ROOT / "evaluation/dinner-date-issue-grouping"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, report_path = folder / f"run-{stamp}.json", folder / f"run-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def clean(value):
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = ["# Dinner Date Observation → Issue 聚合验收", "",
             f"- 冻结基线：50 Review / {summary['input_observations']} Observation / `{BASELINE_SHA256}`",
             f"- 聚合结果：{summary['issue_count']} Issue；{summary['merged_issue_count']} 个合并组；"
             f"{summary['singleton_issue_count']} 个单条组",
             f"- 模型：{result['model']}；请求 {summary['api_calls']} 次；Token {summary['total_tokens']}", ""]
    for number, issue in enumerate(issues, 1):
        lines += [f"## {number}. {issue['title']}", "",
                  f"- `issue_id`: `{issue['issue_id']}`",
                  f"- Category: `{issue['category']}`",
                  f"- 统一描述：{issue['canonical_description']}",
                  f"- Review：{', '.join(f'`{rid}`' for rid in issue['review_ids'])}", "",
                  "| review_id | object / symptom | Observation | evidence |",
                  "|---|---|---|---|"]
        for oid in issue["observation_ids"]:
            row = by_observation[oid]
            lines.append(f"| `{row['review_id']}` | `{row['object']} / {row['symptom']}` | "
                         f"{clean(row['description'])} | {clean(row['evidence'])} |")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(report_path), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
