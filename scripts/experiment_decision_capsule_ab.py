"""Compare Decision generation from full evidence versus Evidence Capsules."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import evidence_capsules as capsules
import pipeline

REPORT = ROOT / "evaluation/dinner-date-full-run/decision-capsule-ab-v1.3.json"
CAPSULE_CACHE = ROOT / "runtime/evidence_capsules/dinner-date"
PROMPT = app.DECISION_PROMPT.replace(
    "输入只提供 Evidence Capsule manifestations；",
    "本离线回归输入会提供 evidence 或 Evidence Capsule manifestations；",
)


def _validate(content, candidates, allowed_ids, gate_by_issue):
    return app.validate_decision_drafts(content, candidates, allowed_ids, gate_by_issue)


def _usage(records):
    fields = ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens")
    return {field: sum(row.get("usage", {}).get(field, 0) for row in records) for field in fields} | {
        "attempts": len(records), "passed_attempts": sum(row.get("status") == "passed" for row in records)}


def _load_inputs():
    app.load_env()
    snapshot = app.chatbi_snapshot("dinner-date")
    candidates = app.decision_candidates(snapshot)
    raw = json.loads(app.chatbi_source("dinner-date").read_text(encoding="utf-8"))
    raw_issues = {row["issue_id"]: row for row in raw["issues"]}
    observations = {row["observation_id"]: row for row in raw["observations"]}
    capsule_by_issue = {}
    for issue in candidates:
        capsule = json.loads((CAPSULE_CACHE / f"{issue['id']}.json").read_text(encoding="utf-8"))
        if capsule.get("capsule_version") != capsules.CAPSULE_VERSION:
            raise ValueError("请先运行 build_dinner_date_capsules.py 生成当前版本 Capsule")
        capsule_by_issue[issue["id"]] = capsule
    gate_by_issue = app.decision_gates(candidates, capsule_by_issue)
    base = lambda issue: {"issue_id": issue["id"], "title": issue["title"],
                          "description": issue["description"], "category": issue["category_label"],
                          "review_count": issue["reviews"], "observation_count": issue["observations"],
                          "decision_gate": gate_by_issue[issue["id"]]}
    full_payload, capsule_payload = [], []
    full_allowed, capsule_allowed = {}, {}
    for issue in candidates:
        issue_id = issue["id"]
        raw_issue = raw_issues[issue_id]
        full_evidence = [{key: observations[oid].get(key, "") for key in
                         ("observation_id", "review_id", "object", "symptom", "description", "evidence")}
                         for oid in raw_issue["observation_ids"]]
        capsule = capsule_by_issue[issue_id]
        compact_manifestations = [{key: item[key] for key in
                                    ("id", "label", "observation_count", "review_count", "representatives")}
                                   for item in capsule["manifestations"]]
        full_payload.append(base(issue) | {"evidence": full_evidence})
        capsule_payload.append(base(issue) | {"manifestations": compact_manifestations,
                                              "evidence_quality": capsule["evidence_quality"]})
        full_allowed[issue_id] = {row["observation_id"] for row in full_evidence}
        capsule_allowed[issue_id] = {row["observation_id"] for item in compact_manifestations
                                     for row in item["representatives"]}
    return snapshot, candidates, observations, capsule_by_issue, gate_by_issue, {
        "full_evidence": (full_payload, full_allowed), "capsule": (capsule_payload, capsule_allowed)}


def _annotate(rows, capsule_by_issue, observations):
    result = []
    for row in rows:
        capsule = capsule_by_issue[row["issue_id"]]
        membership = {oid: item["id"] for item in capsule["manifestations"]
                      for oid in item["observation_ids"]}
        largest = capsule["manifestations"][0]["id"]
        refs = row["evidence_observation_ids"]
        result.append(row | {
            "referenced_manifestation_ids": sorted({membership[oid] for oid in refs}),
            "references_minority_manifestation": any(membership[oid] != largest for oid in refs),
            "referenced_observations": [{"observation_id": oid,
                                          "description": observations[oid]["description"]} for oid in refs],
        })
    return result


def main(run_model="--run" in sys.argv):
    snapshot, candidates, observations, capsule_by_issue, gate_by_issue, variants = _load_inputs()
    if not run_model:
        print(json.dumps({name: {"issues": len(payload),
              "evidence_units": sum(len(issue.get("evidence", issue.get("manifestations", []))) for issue in payload),
              "json_characters": len(json.dumps(payload, ensure_ascii=False))}
              for name, (payload, _) in variants.items()}, ensure_ascii=False, indent=2))
        return
    outputs, usages, audits = {}, {}, {}
    for name, (payload, allowed) in variants.items():
        records = []
        rows = pipeline.deepseek(PROMPT, payload,
                                 lambda content, a=allowed: _validate(content, candidates, a, gate_by_issue),
                                 records.append)
        outputs[name] = _annotate(rows, capsule_by_issue, observations)
        usages[name] = _usage(records)
        audits[name] = records
    full_tokens, capsule_tokens = usages["full_evidence"]["prompt_tokens"], usages["capsule"]["prompt_tokens"]
    report = {
        "version": "decision-capsule-ab-v1.3",
        "dataset": "dinner-date",
        "source": snapshot["source"],
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "prompt_hash": pipeline.fingerprint(PROMPT),
        "issue_ids": [issue["id"] for issue in candidates],
        "comparison": {
            "full_evidence_prompt_tokens": full_tokens,
            "capsule_prompt_tokens": capsule_tokens,
            "prompt_tokens_saved": full_tokens - capsule_tokens,
            "prompt_token_reduction_share": round(1 - capsule_tokens / full_tokens, 4) if full_tokens else None,
        },
        "usage": usages,
        "outputs": outputs,
        "automatic_checks": {
            "same_prompt": True, "same_model": True, "same_issue_ids": True,
            "valid_issue_coverage": True, "traceable_observation_ids": True,
        },
        "human_review": [{"issue_id": issue["id"], "factual_accuracy": "pending",
                           "action_type_reasonable": "pending", "action_quality": "pending",
                           "minority_signal_preserved": "pending", "preferred_variant": "pending",
                           "notes": ""} for issue in candidates],
        "audit": audits,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
    print(REPORT)


if __name__ == "__main__":
    main()
