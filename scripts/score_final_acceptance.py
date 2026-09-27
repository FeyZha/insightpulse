"""Score the submitted independent acceptance form against its frozen sample.

python scripts/score_final_acceptance.py
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def field(block, name):
    match = re.search(r"^" + re.escape(name) + r"：([^\n]*)", block, re.M)
    return match[1].strip().strip("*").strip() if match else ""


def score(text, manifest, source):
    digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if digest != manifest["source_hash"]:
        raise ValueError("Frozen model source changed")
    blocks = re.split(r"^### ([ABC])(\d+)\. ([^\n]+)\s*$", text, flags=re.M)
    sections = {k: [] for k in "ABC"}
    for kind, number, ids, body in zip(blocks[1::4], blocks[2::4], blocks[3::4], blocks[4::4]):
        sections[kind].append(dict(number=int(number), ids=ids.strip(), body=body))
    for kind, expected in [("A", manifest["issue_ids"]), ("B", manifest["coverage_review_ids"]),
                           ("C", [f"{p['a']} / {p['b']}" for p in manifest["pairs"]])]:
        if [r["ids"] for r in sections[kind]] != expected:
            raise ValueError(f"Missing, changed or reordered {kind} sample IDs")
    issues = []
    for row in sections["A"]:
        values = {name: field(row["body"], name) for name in ("支持判断", "分类判断", "严重度判断", "理由")}
        if values["支持判断"] not in ("支持", "不支持", "不确定"):
            raise ValueError("Missing support judgment")
        if any(values[k] not in ("合理", "不合理", "不确定") for k in ("分类判断", "严重度判断")):
            raise ValueError("Missing classification/severity judgment")
        issues.append(dict(id=row["ids"], **values))
    pairs = []
    for row, original in zip(sections["C"], manifest["pairs"]):
        label = field(row["body"], "判断")
        if label not in ("相同", "不同", "不确定"):
            raise ValueError("Missing pair judgment")
        pairs.append(dict(original, judgment=label, reason=field(row["body"], "理由"),
                          correct=None if label == "不确定" else (label == "相同") == original["system_same_group"]))
    omissions = []
    for row in sections["B"]:
        label = field(row["body"], "遗漏问题")
        severity = field(row["body"], "其中是否有无法游玩、数据丢失或安全问题")
        if not label or severity not in ("是", "否", "不确定"):
            raise ValueError("Missing omission/severe-problem judgment")
        omissions.append(dict(id=row["ids"], judgment=label, severe=severity,
                              status="uncertain" if label.startswith("不确定") else "none" if label.startswith("无遗漏") else "omitted"))
    if field(text, "确认以上为本人独立完成") != "确认":
        raise ValueError("Independent human confirmation missing")
    timing = text.split("人工方式：", 1)[1].split("工具方式：", 1)
    duration = [float(field(part, "扣除中断后的分钟数")) for part in timing]
    counts = [int(field(part, "产出事项数")) for part in timing]
    correction = float(field(timing[1], "其中纠错分钟数"))
    if min(duration) <= 0 or min(counts) < 0 or not 0 <= correction <= duration[1]:
        raise ValueError("Invalid reported timing")
    supported = sum(r["支持判断"] == "支持" for r in issues)
    definite = sum(r["支持判断"] != "不确定" for r in issues)
    pair_definite = [r for r in pairs if r["correct"] is not None]
    return dict(provenance="independent_human_confirmed_in_submitted_form", source_run=source["id"],
                source_processing_version=source["processing_version"], issues=issues, pairs=pairs, omissions=omissions,
                support=dict(supported=supported, assessed=definite, assigned=len(issues),
                             rate=supported / definite if definite else None,
                             sample_gate_passed=definite == len(issues) and supported / definite >= .9),
                grouping=dict(correct=sum(r["correct"] for r in pair_definite), assessed=len(pair_definite), assigned=len(pairs)),
                timing=dict(manual_minutes=duration[0], assisted_minutes=duration[1], correction_minutes=correction,
                            produced_items=counts, provenance="single_person_self_report",
                            missing_start_end=any(not field(part, key) for part in timing for key in ("开始时间", "结束时间")),
                            order_note=field(text, "本次采用顺序与熟悉效应备注")),
                limits="Sample support is not end-to-end accuracy. Omission answers do not establish issue-level recall. Timing is a single-person report, without verified timestamps or counterbalanced order. Preserve disagreements and uncertainties.")


if __name__ == "__main__":
    path = ROOT / "docs/FINAL_HUMAN_ACCEPTANCE.md"
    manifest = json.loads((ROOT / "evaluation/final-review-manifest.json").read_text(encoding="utf-8"))
    source = json.loads((ROOT / "evaluation/final-review-source.json").read_text(encoding="utf-8"))
    result = score(path.read_text(encoding="utf-8"), manifest, source)
    result["submitted_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (ROOT / "evaluation/final-human-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("support", "grouping", "timing")}, ensure_ascii=False))
