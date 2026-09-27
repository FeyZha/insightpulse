"""Run the frozen Ostranauts Observations through the selected Issue grouping policy once."""
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

APP_ID = 1022980
FOLDER = ROOT / "evaluation/ostranauts-current"
SOURCE = FOLDER / "product-current.json"
CHECKPOINT = FOLDER / f"issues-{flow.ISSUE_GROUP_VERSION}-130.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def group_all(observations, model, *, source=SOURCE, checkpoint=CHECKPOINT):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest().upper()
    observation_ids = sorted(row["observation_id"] for row in observations)
    prompt_hash = pipeline.fingerprint([flow.ISSUE_GROUP_PROMPT, flow.ISSUE_CONSOLIDATION_PROMPT,
                                        flow.ISSUE_MEMBERSHIP_PROMPT, flow.ISSUE_BOUNDARY_PROMPT])
    contract = (source_hash, model, flow.ISSUE_GROUP_VERSION, prompt_hash,
                flow.ISSUE_GROUP_BATCH_SIZE, observation_ids)
    if checkpoint.exists():
        state = load(checkpoint)
        actual = (state.get("source_sha256"), state.get("model"), state.get("issue_group_version"),
                  state.get("prompt_hash"), state.get("batch_size"), state.get("observation_ids"))
        if actual != contract:
            raise ValueError("Ostranauts Issue checkpoint belongs to another contract")
    else:
        state = {"source_sha256": source_hash, "model": model,
                 "issue_group_version": flow.ISSUE_GROUP_VERSION, "prompt_hash": prompt_hash,
                 "batch_size": flow.ISSUE_GROUP_BATCH_SIZE, "observation_ids": observation_ids,
                 "category_issues": {}, "api_calls": []}

    issues = []
    for category in sorted({row["category"] for row in observations}):
        members = [row for row in observations if row["category"] == category]
        if category not in state["category_issues"]:
            calls = []
            try:
                state["category_issues"][category] = flow.group_observations(
                    members, calls.append, app_id=APP_ID, model=model)
            finally:
                state["api_calls"].extend(calls)
                save(checkpoint, state)
            print(f"issue grouping {category}: {len(members)} observations -> "
                  f"{len(state['category_issues'][category])} issues", flush=True)
        issues.extend(flow.validate_issue_groups(
            json.dumps({"issues": state["category_issues"][category]}, ensure_ascii=False),
            members, APP_ID))

    if "membership_issues" not in state:
        calls = []
        try:
            state["membership_issues"] = flow.audit_large_issue_membership(
                observations, issues, calls.append, app_id=APP_ID, model=model)
        finally:
            state["api_calls"].extend(calls)
            save(checkpoint, state)
    issues = flow.validate_issue_groups(
        json.dumps({"issues": state["membership_issues"]}, ensure_ascii=False), observations, APP_ID)

    if state.get("boundary_version") != flow.ISSUE_BOUNDARY_VERSION:
        calls = []
        try:
            state["audited_issues"] = flow.audit_issue_boundaries(
                observations, issues, calls.append, app_id=APP_ID, model=model)
            state["boundary_version"] = flow.ISSUE_BOUNDARY_VERSION
        finally:
            state["api_calls"].extend(calls)
            save(checkpoint, state)
    issues = flow.validate_issue_groups(
        json.dumps({"issues": state["audited_issues"]}, ensure_ascii=False), observations, APP_ID)
    flow.issue_quality_diagnostics(observations, issues)
    return state, issues


def main():
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("No DeepSeek key; no requests made")
    model = os.environ.get("DEEPSEEK_ISSUE_MODEL", "deepseek-v4-pro")
    product = load(SOURCE)
    observations = product["observations"]
    baseline_issues = flow.validate_issue_groups(
        json.dumps({"issues": product["issues"]}, ensure_ascii=False), observations, APP_ID)
    baseline = flow.issue_quality_diagnostics(observations, baseline_issues)
    state, issues = group_all(observations, model)
    quality = flow.issue_quality_diagnostics(observations, issues)

    result = dict(product)
    result.update({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
        "category_issue_counts": dict(sorted(Counter(row["category"] for row in issues).items())),
        "issue_group_version": flow.ISSUE_GROUP_VERSION,
        "issue_group": {"version": flow.ISSUE_GROUP_VERSION, "model": model,
                        "prompt_hash": state["prompt_hash"], "batch_size": flow.ISSUE_GROUP_BATCH_SIZE,
                        "boundary_version": flow.ISSUE_BOUNDARY_VERSION,
                        "mode": "holistic_within_category"},
        "issue_quality": quality,
        "issue_grouping_comparison": {"baseline": baseline, "after": quality},
    })
    result["summary"] = dict(product["summary"], final_issues=len(issues))
    if "severity" in json.dumps(result, ensure_ascii=False):
        raise ValueError("severity is forbidden")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = FOLDER / f"product-zz-{flow.ISSUE_GROUP_VERSION}-{stamp}.json"
    save(output, result)
    print(json.dumps({"output": str(output), "observations": len(observations),
                      "before_issues": len(baseline_issues), "after_issues": len(issues),
                      "before_singletons": baseline["single_observation_issues"],
                      "after_singletons": quality["single_observation_issues"],
                      "calls": len(state["api_calls"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
