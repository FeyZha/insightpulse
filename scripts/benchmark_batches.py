"""Paid, sequential batch-size comparison. Uses local .env; never logs credentials.

python scripts/benchmark_batches.py evaluation/live-baseline-v1.json
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as p
from app import load_env


PRICING = {"source": "https://api-docs.deepseek.com/quick_start/pricing/", "checked": "2026-09-10",
           "currency": "USD", "unit": "per_million_tokens", "off_peak_flash": {"hit": .007, "miss": .22, "output": .66},
           "off_peak_pro": {"hit": .022, "miss": .66, "output": 1.98},
           "peak": "Weekdays UTC 01:00-04:00 and 06:00-10:00: 2x; request start used for estimate"}


def cost(call, cold=False):
    usage = call.get("usage", {})
    if not call.get("started_at") or any(k not in usage for k in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens")):
        return None
    date = datetime.fromisoformat(call["started_at"]).astimezone(timezone.utc)
    multiplier = 2 if date.weekday() < 5 and (1 <= date.hour < 4 or 6 <= date.hour < 10) else 1
    rates = PRICING["off_peak_pro" if call["model"] == "deepseek-v4-pro" else "off_peak_flash"]
    hit = 0 if cold else usage["prompt_cache_hit_tokens"]
    return multiplier * (hit * rates["hit"] + (usage["prompt_tokens"] - hit) * rates["miss"] +
                         usage["completion_tokens"] * rates["output"]) / 1000000


def summary(run):
    calls = run["calls"]
    usage = {k: sum(c.get("usage", {}).get(k, 0) or 0 for c in calls)
             for k in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens")}
    first_pass = {rid for c in calls if c["attempt"] == 1 and not (c.get("retry_review_id") or c.get("retry_review_ids"))
                  for rid in c.get("valid_review_ids", [])}
    observed = [cost(c) for c in calls]
    cold = [cost(c, True) for c in calls]
    return {"batch_size": run["batch_size"], "round": run["round"], "status": run["status"],
            "passed": len(run["extractions"]), "failed": len(run["failures"]),
            "first_pass": len(first_pass), "requests": len(calls),
            "retry_requests": sum(c["attempt"] > 1 or bool(c.get("retry_review_id") or c.get("retry_review_ids")) for c in calls),
            "truncated": sum(c.get("finish_reason") == "length" for c in calls),
            "complaint_reviews": sum(bool(v) for v in run["extractions"].values()),
            "issues": sum(len(v) for v in run["extractions"].values()),
            "api_seconds": round(sum(c["seconds"] for c in calls), 3),
            "estimated_usd": sum(observed) if all(v is not None for v in observed) else None,
            "no_cache_estimated_usd": sum(cold) if all(v is not None for v in cold) else None,
            "wall_seconds": run.get("wall_seconds"), **usage}


def compare(runs):
    """Prediction disagreement is a diagnostic, never a substitute for human truth."""
    result = []
    for run in runs:
        baseline = next((r for r in runs if r["batch_size"] == 5 and r["round"] == run["round"]), None)
        if not baseline:
            continue
        common = set(baseline["extractions"]) & set(run["extractions"])
        changed = []
        for rid in sorted(common):
            a, b = baseline["extractions"][rid], run["extractions"][rid]
            if {i["aspect"] for i in a} != {i["aspect"] for i in b} or len(a) != len(b):
                changed.append({"id": rid, "baseline": a, "candidate": b})
        result.append({"batch_size": run["batch_size"], "round": run["round"],
                       "comparable_reviews": len(common), "changed": changed})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--sizes", type=int, nargs="+", default=[5, 10, 20, 40, 60, 120])
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--token-budget", type=int, default=250000)
    parser.add_argument("--request-budget", type=int, default=150)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 3 or args.token_budget <= 0 or args.request_budget <= 0:
        parser.error("Invalid test budget")
    for size in args.sizes:
        list(p.review_batches([], size))
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise SystemExit("Missing local DEEPSEEK_API_KEY; no requests made")
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    selected = source["plan"]["selected"]
    prompt = source["extract_prompt"]
    schedule = list(dict.fromkeys(args.sizes))
    random.Random(20260910).shuffle(schedule)
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "source_run": source["id"], "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
              "prompt": prompt, "prompt_hash": p.fingerprint(prompt), "processing_version": p.PROCESSING_VERSION,
              "selected": selected, "input_hash": p.fingerprint(selected),
              "schedule": [schedule if i % 2 == 0 else list(reversed(schedule)) for i in range(args.rounds)],
              "batch_char_limit": p.BATCH_CHAR_LIMIT, "max_output_tokens": 8192,
              "token_budget": args.token_budget, "request_budget": args.request_budget,
              "pricing": PRICING,
              "cache_policy": "No application cache; provider prefix cache recorded separately",
              "runs": [], "limits": "开发诊断，非独立人工质量评测。两次重复不能证明生产可靠性。成本含重试，软用量阈值可被最后一次响应越过。"}
    destination = Path("evaluation") / ("batch-benchmark-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    def persist():
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    def check_budget():
        calls = [c for r in result["runs"] for c in r["calls"]]
        tokens = sum(c.get("usage", {}).get("total_tokens", 0) or 0 for c in calls)
        if tokens >= args.token_budget or len(calls) >= args.request_budget:
            raise RuntimeError("Comparison budget reached; no further requests")
    print(str(destination), flush=True)
    current = None
    try:
        for round_number, sizes in enumerate(result["schedule"], 1):
            for size in sizes:
                check_budget()
                current = {"batch_size": size, "round": round_number, "status": "running",
                           "extractions": {}, "failures": {}, "calls": [], "batches": []}
                result["runs"].append(current)
                started = time.monotonic()
                for batch in p.review_batches(selected, size):
                    check_budget()
                    ids = [r["id"] for r in batch]
                    current["batches"].append(ids)
                    def audit(call):
                        call["batch_ids"] = ids
                        current["calls"].append(call)
                        persist()  # Save responses even if a later request is interrupted.
                        check_budget()
                    try:
                        output, failed = p.extract_batch(prompt, batch, audit)
                        current["extractions"].update(output)
                        current["failures"].update(failed)
                    except ValueError as exc:
                        current["failures"].update({rid: str(exc) for rid in ids})
                    current["wall_seconds"] = round(time.monotonic() - started, 3)
                    persist()
                    if any(code in error for error in current["failures"].values()
                           for code in ("HTTP 401", "HTTP 402", "HTTP 403")):
                        raise RuntimeError("Credential/balance error; comparison stopped")
                current["status"] = "partial" if current["failures"] else "complete"
                current["summary"] = summary(current)
                persist()
                print(json.dumps(current["summary"]), flush=True)
        result["status"] = "complete"
    except (RuntimeError, KeyboardInterrupt) as exc:
        result["status"] = "interrupted"
        result["error"] = str(exc)
        if current and current["status"] == "running":
            current["status"] = "interrupted"
    finally:
        result["comparisons"] = compare(result["runs"])
        persist()
    print("Saved: " + str(destination), flush=True)


if __name__ == "__main__":
    main()
