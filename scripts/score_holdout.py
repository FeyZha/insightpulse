"""Reproduce the frozen holdout comparison without calling AI or changing labels.

python scripts/score_holdout.py
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline as p
from evaluation import score
from scripts.score_submitted_review import presence

FILES = {
    "current": "batch-benchmark-20260910T065444904018Z.json",
    "candidate": "batch-benchmark-20260910T065716497135Z.json",
}
ASPECTS = dict(zip(
    ["性能", "稳定性", "网络", "玩法", "平衡", "内容", "界面/操作", "付费", "本地化", "反作弊", "其他"],
    ["Performance", "Stability", "Network", "Gameplay", "Balance", "Content", "UX", "Monetization", "Localization", "Anti-cheat", "Other"],
))


def read(name):
    return json.loads((ROOT / "evaluation" / name).read_text(encoding="utf-8"))


def main():
    submitted = read("holdout-submitted-labels.json")
    freeze = read("holdout-freeze-v1.json")
    assert hashlib.sha256((ROOT / "docs/BLIND_REVIEW_HOLDOUT.md").read_bytes()).hexdigest() == submitted["source_sha256"]
    assert hashlib.sha256((ROOT / "evaluation/frozen-processing-v1.3.py").read_bytes()).hexdigest() == freeze["pipeline_sha256"]
    gold = read("gold-v3.json")
    labels = {r["id"]: r for r in submitted["rows"]}
    assert set(labels) == set(freeze["holdout_ids"]) and len(labels) == 40
    normalizations, exclusions = [], []
    for row in gold["reviews"]:
        if row["split"] != "holdout":
            continue
        rid = row["id"]
        assert p.fingerprint(row["text"]) == freeze["holdout_text_hashes"][rid]
        label = labels[rid]
        row["submitted_annotation"] = label
        expected, reasons = [], []
        if label["judgment"] == "不确定":
            reasons.append("uncertain_judgment")
        for index, issue in enumerate(label["issues"], 1):
            aspect = ASPECTS.get(issue["aspect_raw"])
            if aspect is None:
                reasons.append(f"issue_{index}:ambiguous_aspect:{issue['aspect_raw']}")
            try:
                quote = p.align_evidence(issue["evidence"], row["text"])
            except ValueError:
                reasons.append(f"issue_{index}:non_contiguous_or_changed_quote")
                continue
            if quote != issue["evidence"]:
                normalizations.append(dict(id=rid, issue_index=index, original=issue["evidence"], restored=quote, rule="unique_whitespace_only"))
            expected.append(dict(issue=issue["issue"], aspect=aspect, evidence=quote))
        row["expected"] = None if reasons else expected
        row["reviewer"] = None if reasons else "independent-human-confirmed-by-user"
        row["annotation_status"] = "unresolved" if reasons else "imported"
        if reasons:
            exclusions.append(dict(id=rid, reasons=reasons))
    gold.update(version="gold-v4", parent_version="gold-v3", origin="independent_human_dev_and_partial_holdout",
                holdout_annotation_source_sha256=submitted["source_sha256"], holdout_normalizations=normalizations,
                holdout_exclusions=exclusions)
    results = []
    for variant, filename in FILES.items():
        benchmark = read(filename)
        assert benchmark["prompt"] == freeze["prompts"][variant]
        assert benchmark["model"] == freeze["model"]
        assert benchmark["processing_version"] == freeze["processing_version"]
        assert [r["id"] for r in benchmark["selected"]] == freeze["holdout_ids"]
        assert all(p.fingerprint(r["text"]) == freeze["holdout_text_hashes"][r["id"]] for r in benchmark["selected"])
        for run in benchmark["runs"]:
            assert run["batch_size"] == freeze["batch_size"]
            binary = presence(list(labels.values()), run["extractions"])
            # Missing outputs are delivery failures, never successful negative predictions.
            denominator = binary["assessed"] + len(binary["missing_predictions"])
            binary["end_to_end_correct"] = binary["tp"] + binary["tn"]
            binary["end_to_end_assigned"] = denominator
            binary["end_to_end_accuracy"] = binary["end_to_end_correct"] / denominator
            aspects = score(gold, {"plan": {"selected": benchmark["selected"]}, "model_extractions": run["extractions"]}, "holdout")
            all_issues = [i for issues in run["extractions"].values() for i in issues]
            texts = {r["id"]: r["text"] for r in benchmark["selected"]}
            valid = sum(i["evidence"] in texts[rid] for rid, issues in run["extractions"].items() for i in issues)
            assert valid == len(all_issues)
            results.append(dict(variant=variant, round=run["round"], source=filename, summary=run["summary"],
                                presence=binary, strict_aspect=aspects, failures=run["failures"],
                                accepted_evidence=dict(valid=valid, total=len(all_issues))))
    output = dict(decision="retain_current_no_promotion", provenance=submitted["provenance"],
                  source_sha256=submitted["source_sha256"], freeze="holdout-freeze-v1.json", results=results,
                  normalizations=normalizations, strict_exclusions=exclusions,
                  limits="Presence is not issue accuracy. Strict aspect excludes unresolved whole reviews; failures count as missed positive aspects. Evidence matches are checked after validation and do not prove semantic support. This exposed holdout must not be reused as an unseen test after tuning.")
    for name, value in [("gold-v4.json", gold), ("holdout-results-v1.json", output)]:
        (ROOT / "evaluation" / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print(r["variant"], r["round"], r["presence"]["end_to_end_accuracy"], r["strict_aspect"]["evaluated"], r["strict_aspect"]["micro_aspect"])
    print("strict exclusions", len(exclusions), "whitespace restorations", len(normalizations))


if __name__ == "__main__":
    main()
