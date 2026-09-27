"""Prepare independent human labels and score an immutable model run.

python evaluation.py prepare data/steam-....json
python evaluation.py score evaluation/gold.json RUN_ID --split holdout
"""
import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import os
from datetime import datetime, timezone

import pipeline as p
from pipeline import ASPECTS, fingerprint

ROOT = Path(__file__).resolve().parent


def prepare_gold(snapshot, count=60):
    buckets = defaultdict(list)
    unique = {r["id"]: r for r in snapshot["reviews"]}
    for review in unique.values():
        if review["updated"] > review["created"] or not review["text"].strip() or len(review["text"]) > 12000:
            continue
        # Human labeling starts with Chinese/English. Other languages are an explicit coverage gap.
        if review["language"] not in ("english", "schinese", "tchinese"):
            continue
        buckets[(review["language"], review["recommended"], len(review["text"]) > 400)].append(review)
    for key in buckets:
        buckets[key].sort(key=lambda r: fingerprint(r["id"]))
    picked = []
    while len(picked) < count and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(picked) < count:
                picked.append(buckets[key].pop(0))
    rows = [dict(id=r["id"], text=r["text"], text_hash=fingerprint(r["text"]),
                 language=r["language"], recommended=r["recommended"],
                 split="dev" if i % 3 == 0 else "holdout", expected=None,
                 reviewer=None, notes="") for i, r in enumerate(picked)]
    return {"version": "gold-v1", "origin": "unlabeled_real_reviews", "app_id": snapshot["app_id"],
            "instructions": "人工独立阅读。expected=null 表示未标；[]=无具体投诉；否则填写 aspect、issue、evidence。evidence 必须为原文连续片段。填写 reviewer，不能把模型建议当人工真值。",
            "sampling": "语言 × 推荐状态 × 文本长短分层轮转；开发集20/留出40（足量时）。不是玩家总体代表样本。",
            "reviews": rows}


def score(gold, run, split):
    rows = [r for r in gold["reviews"] if r["split"] == split]
    labeled = [r for r in rows if r.get("expected") is not None and r.get("reviewer")]
    predictions = run.get("model_extractions", {})
    inputs = {r["id"]: r for r in run["plan"]["selected"]}
    counts = {a: {"tp": 0, "fp": 0, "fn": 0} for a in ASPECTS}
    bad_cases, missing, hash_mismatch, evaluated, evidence_total, evidence_valid = [], [], [], 0, 0, 0
    for row in labeled:
        rid = row["id"]
        if rid not in inputs:
            missing.append(rid)
            continue
        if fingerprint(inputs[rid]["text"]) != row["text_hash"]:
            hash_mismatch.append(rid)
            continue
        expected = row["expected"]
        if not isinstance(expected, list):
            raise ValueError("人工标注 expected 必须为数组")
        for item in expected:
            if item.get("aspect") not in ASPECTS or not item.get("issue") or not item.get("evidence") or item["evidence"] not in row["text"]:
                raise ValueError(f"人工标注 {rid} 不符合规范")
        evaluated += 1
        # Failed selected inputs count as missed labels, not silently excluded from recall.
        predicted = predictions.get(rid, [])
        expected_aspects = {i["aspect"] for i in expected}
        predicted_aspects = {i["aspect"] for i in predicted}
        for aspect in ASPECTS:
            counts[aspect]["tp"] += int(aspect in expected_aspects & predicted_aspects)
            counts[aspect]["fp"] += int(aspect in predicted_aspects - expected_aspects)
            counts[aspect]["fn"] += int(aspect in expected_aspects - predicted_aspects)
        for item in predicted:
            evidence_total += 1
            evidence_valid += bool(item.get("evidence") and item["evidence"] in row["text"])
        if expected_aspects != predicted_aspects or rid not in predictions:
            bad_cases.append({"id": rid, "kind": "request_failed" if rid not in predictions else "aspect_mismatch",
                              "text": row["text"], "expected": expected, "predicted": predicted})
    def rates(c):
        precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else None
        recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
        denom = 2 * c["tp"] + c["fp"] + c["fn"]
        return dict(c, precision=precision, recall=recall, f1=2*c["tp"]/denom if denom else None)
    total = {k: sum(c[k] for c in counts.values()) for k in ("tp", "fp", "fn")}
    return {"split": split, "assigned": len(rows), "human_labeled": len(labeled), "evaluated": evaluated,
            "status": "complete" if evaluated == len(rows) and rows else "incomplete",
            "micro_aspect": rates(total), "per_aspect": {a: rates(c) for a, c in counts.items()},
            "post_validation_evidence_match": evidence_valid/evidence_total if evidence_total else None,
            "missing_from_run": missing, "changed_inputs": hash_mismatch, "bad_cases": bad_cases,
            "limits": "只评方面识别，不等于具体问题正确率或分组质量；原文匹配只证明摘录存在。语义支持、严重问题漏检、分组合并/拆分需独立人工复核。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("snapshot")
    execute = sub.add_parser("run")
    execute.add_argument("gold")
    execute.add_argument("--split", choices=["dev", "holdout"], default="dev")
    execute.add_argument("--variant", choices=["baseline", "guided"], default="guided")
    execute.add_argument("--model", choices=["deepseek-v4-flash", "deepseek-v4-pro"], default="deepseek-v4-flash")
    execute.add_argument("--batch-size", type=int, default=p.DEFAULT_BATCH_SIZE)
    grade = sub.add_parser("score")
    grade.add_argument("gold")
    grade.add_argument("run_id")
    grade.add_argument("--split", choices=["dev", "holdout"], default="holdout")
    args = parser.parse_args()
    folder = ROOT / "evaluation"
    folder.mkdir(exist_ok=True)
    if args.command == "prepare":
        result = prepare_gold(json.loads(Path(args.snapshot).read_text(encoding="utf-8")))
        result["snapshot"] = Path(args.snapshot).name
        destination = folder / "gold.json"
    elif args.command == "run":
        from app import load_env
        load_env()
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise SystemExit("Set DEEPSEEK_API_KEY in local .env before running a paid evaluation.")
        os.environ["DEEPSEEK_MODEL"] = args.model
        gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
        selected = [r for r in gold["reviews"] if r["split"] == args.split]
        batches = list(p.review_batches(selected, args.batch_size))
        baseline = ('Read the game reviews and extract concrete complaints. Return json with one result per id: '
                    '{"reviews":[{"id":"...","issues":[{"title":"short Chinese problem title",'
                    '"aspect":"one of ' + ', '.join(ASPECTS) + '","severity":"low/medium/high/critical",'
                    '"evidence":"exact contiguous original quote"}]}]}. Return issues:[] when none.')
        prompt = p.EXTRACT_PROMPT if args.variant == "guided" else baseline
        result = {"model": args.model, "variant": args.variant, "prompt": prompt,
                  "processing_version": p.PROCESSING_VERSION,
                  "batch_size": args.batch_size, "batch_char_limit": p.BATCH_CHAR_LIMIT,
                  "prompt_hash": fingerprint(prompt), "gold_version": gold["version"],
                  "gold_input_hash": fingerprint([{k:r[k] for k in ("id","text_hash","split")} for r in gold["reviews"]]),
                  "split": args.split, "plan": {"selected": selected},
                  "model_extractions": {}, "calls": [], "failures": {}}
        destination = folder / f"predictions-{args.variant}-{args.split}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        # Preserve incomplete runs even on interruption; no cached outputs in comparative evals.
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        def persist():
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            os.replace(temporary,destination)
        offset = 0
        for batch in batches:
            offset += len(batch)
            try:
                output, failures = p.extract_batch(prompt,batch,result["calls"].append)
                result["model_extractions"].update(output)
                result["failures"].update(failures)
            except ValueError as exc:
                result["failures"].update({r["id"]:str(exc) for r in batch})
            if any(code in error for error in result["failures"].values()
                   for code in ("HTTP 401","HTTP 402","HTTP 403")):
                result["failures"].update({r["id"]:"凭据或余额错误，未继续请求" for r in selected[offset:]})
                persist()
                break
            persist()
            print(f"Processed {offset}/{len(selected)}",flush=True)
        print(destination)
        raise SystemExit(0 if len(result["model_extractions"])==len(selected) else 1)
    else:
        prediction_path = Path(args.run_id)
        if prediction_path.is_file():
            prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
            label = prediction_path.stem
        else:
            with closing(sqlite3.connect(f"file:{(ROOT / 'runtime' / 'insightpulse.sqlite3').as_posix()}?mode=ro", uri=True)) as conn:
                row = conn.execute("SELECT body FROM runs WHERE id=?", (args.run_id,)).fetchone()
            if not row:
                raise SystemExit("Run not found")
            prediction, label = json.loads(row[0]), args.run_id
        result = score(json.loads(Path(args.gold).read_text(encoding="utf-8")), prediction, args.split)
        destination = folder / f"score-{label}-{args.split}.json"
    # Do not overwrite human labels or previously published evaluation results.
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(destination)
