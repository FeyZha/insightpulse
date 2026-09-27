"""Repeat Decision Gate generation to measure core direction stability."""
from itertools import combinations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import pipeline

REPORT = ROOT / "evaluation/dinner-date-full-run/decision-gate-stability-v1.2.json"


def _jaccard(left, right):
    left, right = set(left), set(right)
    return len(left & right) / len(left | right) if left or right else 1.0


def main(run_model="--run" in sys.argv, repeats=3):
    app.load_env()
    snapshot = app.chatbi_snapshot("dinner-date")
    candidates = app.decision_candidates(snapshot)
    capsule_by_issue = app.decision_capsules(snapshot, candidates)
    gate_by_issue = app.decision_gates(candidates, capsule_by_issue)
    payload = app.decision_payload(candidates, capsule_by_issue, gate_by_issue)
    allowed = {issue_id: {representative["observation_id"] for manifestation in capsule["manifestations"]
               for representative in manifestation["representatives"]}
               for issue_id, capsule in capsule_by_issue.items()}
    if not run_model:
        print(json.dumps({"issues": len(candidates), "repeats": repeats,
                          "estimated_calls": repeats}, ensure_ascii=False))
        return
    runs, audits = [], []
    for _ in range(repeats):
        records = []
        runs.append(pipeline.deepseek(
            app.DECISION_PROMPT, payload,
            lambda content: app.validate_decision_drafts(content, candidates, allowed, gate_by_issue),
            records.append))
        audits.append(records)
    by_issue = []
    for issue in candidates:
        rows = [next(row for row in run if row["issue_id"] == issue["id"]) for run in runs]
        basis_pairs = list(combinations((row["basis_dimensions"] for row in rows), 2))
        by_issue.append({
            "issue_id": issue["id"], "title": issue["title"],
            "types": [row["type"] for row in rows],
            "type_stable": len({row["type"] for row in rows}) == 1,
            "minimum_basis_jaccard": round(min((_jaccard(*pair) for pair in basis_pairs), default=1.0), 4),
            "actions": [row["action"] for row in rows],
            "rationales": [row["rationale"] for row in rows],
            "human_direction_review": "pending",
        })
    report = {
        "version": "decision-gate-stability-v1.2", "decision_version": app.DECISION_VERSION,
        "gate_version": app.DECISION_GATE_VERSION, "dataset": "dinner-date", "repeats": repeats,
        "summary": {"type_stable_issues": sum(row["type_stable"] for row in by_issue),
                    "issues": len(by_issue),
                    "prompt_tokens": sum(record.get("usage", {}).get("prompt_tokens", 0)
                                         for records in audits for record in records)},
        "issues": by_issue, "runs": runs, "audit": audits,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(REPORT)


if __name__ == "__main__":
    main()
