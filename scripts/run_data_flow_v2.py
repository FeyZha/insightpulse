"""Manual, bounded processing of review windows around a game update.

Offline plan: python scripts/run_data_flow_v2.py --event-at 2026-08-25T09:01:00Z
Paid extraction: same command with --live --limit 40
Reuse immutable v2 cache/catalog: --reuse evaluation/data-flow-v2/....json
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import data_flow_v2 as flow
import pipeline as legacy
from app import load_env

APP_ID = flow.APP_ID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="*")
    parser.add_argument("--event-at", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=40, help="Unique reviews, balanced across event windows")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--reuse")
    parser.add_argument("--rules", default="config/issue_status_v2.json")
    args = parser.parse_args()
    if not 1 <= args.limit <= 400:
        parser.error("limit must be 1..400")
    paths = [Path(x) for x in args.snapshots] if args.snapshots else sorted((ROOT / "data").glob(f"steam-{APP_ID}-*.json"))
    snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if not snapshots or {s.get("snapshot", s).get("app_id") for s in snapshots} != {APP_ID}:
        parser.error("This pipeline only accepts Dinner Date (app_id 94000) snapshots")
    event = float(args.event_at) if args.event_at.replace(".", "", 1).isdigit() else args.event_at
    plan = flow.build_plan(snapshots, event, args.days)
    rules = flow.status_rules(json.loads(Path(args.rules).read_text(encoding="utf-8")))
    buckets = [sorted([r for r in plan["reviews"] if r["window"] == window],
                      key=lambda r: legacy.fingerprint(r["review_id"]))
               for window in ("before_update", "after_update")]
    selected = []
    while len(selected) < args.limit and any(buckets):
        for bucket in buckets:
            if bucket and len(selected) < args.limit:
                selected.append(bucket.pop(0))
    vids = {row["review_version_id"] for row in selected}
    reused = json.loads(Path(args.reuse).read_text(encoding="utf-8")) if args.reuse else {}
    if reused and reused.get("processing_version") != flow.PROCESSING_VERSION:
        raise ValueError("Only a v2 run can supply a v2 extraction cache/registry")
    calls = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "evaluation/data-flow-v2"
    folder.mkdir(exist_ok=True)
    destination = folder / f"run-{stamp}.json"
    result = dict(processing_version=flow.PROCESSING_VERSION, prompt_version=flow.PROMPT_VERSION,
                  prompt=flow.OBSERVATION_PROMPT, prompt_hash=legacy.fingerprint(flow.OBSERVATION_PROMPT),
                  status="running", created_at=datetime.now(timezone.utc).isoformat(), plan=plan,
                  snapshot_files=[str(p) for p in paths], snapshot_hashes=[legacy.fingerprint(s) for s in snapshots],
                  reuse_file=args.reuse, reuse_hash=legacy.fingerprint(reused) if reused else None,
                  implementation_hashes={name: legacy.fingerprint((ROOT / name).read_text(encoding="utf-8"))
                                         for name in ("data_flow_v2.py", "pipeline.py", "scripts/run_data_flow_v2.py")},
                  selected_review_ids=[r["review_id"] for r in selected], selected_version_ids=sorted(vids), calls=calls,
                  limits="Bounded real-data run, not accuracy evaluation. Review revisions are not game-update cohorts. Only Issue status, no publish/review decision.")
    def persist():
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    def audit(call):
        calls.append(call)
        persist()
    persist()
    print(destination, flush=True)
    cache = reused.get("extraction_cache", {})
    registry = reused.get("issue_registry")
    outputs, failed = {}, {}
    try:
        if args.live:
            load_env()
            if not os.environ.get("DEEPSEEK_API_KEY"):
                raise ValueError("No DeepSeek key; no requests made")
        model = os.environ.get("DEEPSEEK_MODEL", reused.get("model", "deepseek-v4-flash"))
        result["model"] = model
        if args.live:
            outputs, failed, cache = flow.extract_versions(plan, vids, model, cache, audit)
        else:
            # Offline means no implicit provider request, even if a local key is configured.
            for vid in vids:
                version = plan["review_versions"][vid]
                key = legacy.fingerprint([flow.PROMPT_VERSION, flow.OBSERVATION_PROMPT, model, version["review_id"], version["text"]])
                if key in cache:
                    outputs[vid] = flow.validate_observations(json.dumps({"reviews": [{"id": vid, "issues": cache[key]}]}),
                                                             [{"id": vid, "text": version["text"]}])[vid]
        result.update(flow.assemble(plan, outputs, failed, registry, rules))
        result.update(extraction_cache=cache, status="selected_complete" if len(outputs) == len(vids) else "partial",
                      population_complete=not any(result["summary"]["unprocessed"].values()),
                      api_requests=len(calls), total_tokens=sum(c.get("usage", {}).get("total_tokens", 0) for c in calls),
                      api_seconds=round(sum(c.get("seconds", 0) for c in calls), 3))
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        persist()
    print(json.dumps(dict(result["summary"], selected_reviews=len(selected), selected_versions=len(vids),
                          api_requests=len(calls), total_tokens=result["total_tokens"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
