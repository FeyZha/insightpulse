"""Read submitted Markdown labels; score complaint presence without inventing gold labels.

python scripts/score_submitted_review.py OUTPUT_JSON
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def parse(text, originals):
    blocks = re.split(r"^## \d+\. 评论 (\d+)\s*$", text, flags=re.M)
    rows = []
    for rid, body in zip(blocks[1::2], blocks[2::2]):
        if rid not in originals or any(r["id"] == rid for r in rows):
            raise ValueError("Unknown or duplicate review ID")
        judgment = re.search(r"^判断（[^\n]+）：(.+)$", body, re.M)
        if not judgment or judgment[1].strip() not in ("有具体投诉", "无具体投诉", "不确定"):
            raise ValueError("Missing or invalid judgment")
        answers = body.split("问题清单（问题描述｜方面｜连续原文引用）：", 1)[1].split("仍需核实或有歧义的地方：", 1)
        issues = [{"issue": desc.strip(), "aspect_raw": aspect.strip(), "evidence": evidence.strip(),
                   "exact_quote": evidence.strip() in originals[rid]["text"]}
                  for desc, aspect, evidence in re.findall(r"^\d+\.\s*(.*?)｜([^｜]+)｜(.+)$", answers[0], re.M)]
        value = judgment[1].strip()
        if (value == "有具体投诉" and not issues) or (value != "有具体投诉" and issues):
            raise ValueError("Judgment and issue list conflict")
        rows.append({"id": rid, "judgment": value, "issues": issues, "notes": answers[1].strip()})
    if {r["id"] for r in rows} != set(originals):
        raise ValueError("Missing review rows")
    return rows


def presence(rows, predictions):
    counts = dict(tp=0, fp=0, tn=0, fn=0)
    wrong, uncertain, missing = [], [], []
    for row in rows:
        rid = row["id"]
        if row["judgment"] == "不确定":
            uncertain.append(rid)
            continue
        if rid not in predictions:
            missing.append(rid)
            continue
        expected, predicted = row["judgment"] == "有具体投诉", bool(predictions[rid])
        counts["tp" if expected and predicted else "fn" if expected else "fp" if predicted else "tn"] += 1
        if expected != predicted:
            wrong.append(rid)
    total = sum(counts.values())
    return dict(counts, assessed=total, accuracy=(counts["tp"]+counts["tn"])/total if total else None,
                wrong_ids=wrong, excluded_uncertain_ids=uncertain, missing_predictions=missing,
                status="incomplete" if missing or not total else "preliminary")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/BLIND_REVIEW_DEV.md"
    text = source.read_text(encoding="utf-8")
    gold = json.loads((root / "evaluation/gold-v2.json").read_text(encoding="utf-8"))
    rows = parse(text, {r["id"]: r for r in gold["reviews"] if r["split"] == "dev"})
    results = []
    for variant, filename in [("current", "batch-benchmark-20260910T051638067392Z.json"),
                              ("candidate", "batch-benchmark-20260910T051711838066Z.json")]:
        benchmark = json.loads((root / "evaluation" / filename).read_text(encoding="utf-8"))
        for run in benchmark["runs"]:
            results.append(dict(presence(rows, run["extractions"]), variant=variant, round=run["round"]))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    provenance_path = root / "evaluation/label-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
    confirmed = provenance.get("source_sha256") == source_hash and provenance.get("status") == "independent_human_confirmed"
    result = {"source": str(source), "source_sha256": source_hash,
              "provenance": provenance if confirmed else "user-submitted; independence not established for this file version",
              "rows": rows, "results": results,
              "limits": "有无投诉的初步对照，不是具体问题准确率。未自动消解多方面标签、歧义或修改引用；未修改 gold-v2。"}
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print([(r["variant"], r["round"], r["assessed"], r["accuracy"]) for r in results])
