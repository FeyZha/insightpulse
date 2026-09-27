"""Replay the real missing-ID failure; --live pays for only the missing review.

python scripts/verify_missing_ids.py [--live]
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as p
from app import load_env
from scripts.benchmark_batches import cost


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    source = Path("evaluation/batch-benchmark-20260910T065716497135Z.json")
    benchmark = json.loads(source.read_text(encoding="utf-8"))
    original = next(c for c in benchmark["runs"][1]["calls"] if c["status"] == "failed")
    batch = [r for r in benchmark["selected"] if r["id"] in original["batch_ids"]]
    assert original["finish_reason"] == "stop" and p.fingerprint(batch) == original["input_hash"]
    preserved, missing, repairs = p.partition_extraction(original["content"], batch)
    assert len(preserved) == 9 and set(missing) == {"233669443"}
    calls, payloads = [], []
    real_request = p.deepseek
    if args.live:
        load_env()

    def replay_then_retry(prompt, payload, validator, audit, attempts=2):
        payloads.append([r["id"] for r in payload])
        if len(payloads) == 1:
            result = validator(original["content"])
            audit(dict(status="passed", content=original["content"], kind="saved_response_replay"))
            return result
        assert payloads[-1] == ["233669443"] and attempts == 1
        if not args.live:
            raise ValueError("offline: deliberately failed retry")
        return real_request(prompt, payload, validator, audit, attempts=attempts)

    with patch.object(p, "deepseek", replay_then_retry):
        output, failures = p.extract_batch(benchmark["prompt"], batch, calls.append)
    assert all(output[rid] == issues for rid, issues in preserved.items())
    assert set(output) | set(failures) == set(original["batch_ids"])
    assert not set(output) & set(failures)
    live_calls = [c for c in calls if c.get("kind") != "saved_response_replay"]
    result = dict(kind="saved_failure_replay_with_live_retry" if args.live else "offline_failed_retry_replay",
                  source=str(source), processing_version=p.PROCESSING_VERSION, prompt_hash=p.fingerprint(benchmark["prompt"]),
                  old_batch_delivered=0, preserved_before_retry=len(preserved), requested_ids=payloads,
                  new_api_requests=len(live_calls), total_tokens=sum(c.get("usage", {}).get("total_tokens", 0) for c in live_calls),
                  estimated_usd=sum(cost(c) or 0 for c in live_calls), extractions=output, failures=failures, calls=calls,
                  limits="First response is historical replay; only missing-ID retry is live. Not an independent semantic evaluation or a new ten-review model run.")
    destination = Path("evaluation") / ("missing-id-verification-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destination, flush=True)
    print(json.dumps({k: result[k] for k in ("preserved_before_retry", "new_api_requests", "total_tokens", "estimated_usd", "failures")}), flush=True)
