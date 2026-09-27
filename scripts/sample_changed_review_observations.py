"""Extract observations from representative same-ID, different-text Dinner Date pairs."""
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

PAIR_REVIEW_IDS = ("2298516", "13950693", "9461342")
EXTRA_REVIEW_IDS = ("811384", "1847211", "7881838", "25197093", "847629", "288934")


def main():
    paths = [ROOT / "data/steam-94000-history-20250901.json",
             ROOT / "data/steam-94000-current-20260911.json"]
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    dataset = flow.load_snapshots(snapshots)
    changes, _ = flow.review_history(dataset)
    versions = dataset["review_versions"]
    by_id = {row["review_id"]: row for row in changes if row["change_type"] == "source_text_changed"}
    selected = [by_id[review_id] for review_id in PAIR_REVIEW_IDS]
    current_snapshot_id = snapshots[1]["snapshot"]["snapshot_id"]
    current_versions = {row["review_id"]: row["review_version_id"] for row in dataset["sightings"]
                        if row["snapshot_id"] == current_snapshot_id}
    assert len(selected) == len(PAIR_REVIEW_IDS) and all(rid in current_versions for rid in EXTRA_REVIEW_IDS)
    old = next((json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((ROOT / "evaluation/dinner-date-observation-test").glob("run-*.json"), reverse=True)
                if json.loads(path.read_text(encoding="utf-8")).get("prompt_version") == "observation-v2.1"), None)
    old_observations = {(pair["review_id"], side): pair[side]["observations"]
                        for pair in (old or {}).get("pairs", []) for side in ("historical", "current")}

    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    version_ids = ({vid for row in selected for vid in (row["from_version_id"], row["to_version_id"])}
                   | {current_versions[rid] for rid in EXTRA_REVIEW_IDS})
    calls = []
    observations, failures, _ = flow.extract_versions(dataset, version_ids, model, audit=calls.append)

    pairs = []
    for row in selected:
        pair = {"review_id": row["review_id"], "classification": row["change_type"]}
        for side, key in (("historical", "from_version_id"), ("current", "to_version_id")):
            version_id = row[key]
            pair[side] = {
                "version_id": version_id,
                "text": versions[version_id]["text"],
                "old_observations": old_observations.get((row["review_id"], side)),
                "categories": observations.get(version_id, {}).get("categories"),
                "observations": observations.get(version_id, {}).get("observations"),
                "failure": failures.get(version_id),
            }
        pairs.append(pair)

    singles = []
    for review_id in EXTRA_REVIEW_IDS:
        version_id = current_versions[review_id]
        singles.append({"review_id": review_id, "version_id": version_id, "text": versions[version_id]["text"],
                        "categories": observations.get(version_id, {}).get("categories"),
                        "observations": observations.get(version_id, {}).get("observations"),
                        "failure": failures.get(version_id)})

    result = {
        "purpose": "Category-routed Observation extraction on Dinner Date reviews",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "processing_version": flow.PROCESSING_VERSION,
        "prompt_version": flow.PROMPT_VERSION,
        "model": model,
        "selection": "Three source_text_changed pairs plus four current reviews covering other problem types",
        "source_files": [str(path) for path in paths],
        "pairs": pairs,
        "additional_reviews": singles,
        "api_calls": calls,
        "summary": {
            "pairs": len(pairs),
            "additional_reviews": len(singles),
            "versions": len(version_ids),
            "successful_versions": len(observations),
            "failed_versions": len(failures),
            "total_tokens": sum(call.get("usage", {}).get("total_tokens", 0) for call in calls),
        },
    }
    folder = ROOT / "evaluation/dinner-date-observation-test"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destination)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
