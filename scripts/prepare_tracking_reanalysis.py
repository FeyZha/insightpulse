"""Reuse audited Dinner Date analysis offline; --analyze requires explicit user approval.

Writes only tracking derived data. Never collects or changes a historical snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tracking
import tracking_views


def prepare(root=ROOT, state=None):
    # _seed validates extraction prompts, review text, product source and memberships.
    verified = dict(tracking._seed(root), game="dinner-date")
    files = [root / "evaluation/dinner-date-full-run/observations-v2.3-830.json",
             sorted((root / "evaluation/dinner-date-full-run").glob("run-*.json"))[-1],
             *sorted((root / "evaluation/dinner-date-observation-test").glob("run-*.json"))]
    if state is None:
        import app
        state = app.product_state("dinner-date")
    completed = tracking.load_reanalysis(root, state)
    if completed.get("reanalysis") and completed.get("model") == verified["model"]:
        # The saved digest and original-version bindings were checked by load_reanalysis.
        # Reuse extraction only; a changed issue directory still requires new membership.
        for snap in state["snapshots"]:
            for version in snap["reviews"].values():
                key = tracking._cache_key(version)
                if key not in verified["cache"] and key in completed["cache"]:
                    verified["cache"][key] = tracking._valid_extraction(version["review_id"], version["text"], completed["cache"][key])
        files.append(tracking.reanalysis_path(root, state["game"]))
    runtime_path = tracking._path(root)
    if runtime_path.exists():
        runtime = tracking._read(runtime_path)
        # Existing completion journal pins these extra extractions to original versions.
        # Its grouping calls lack a fixed-definition reference: do NOT reuse those memberships.
        if runtime.get("model") == verified["model"] and runtime.get("analysis_version") == verified["analysis_version"]:
            for done in runtime.get("calls", []):
                if done.get("operation") != "historical_missing_analysis_completed":
                    continue
                started = next((c for c in runtime["calls"] if c.get("operation") == "historical_missing_analysis"
                    and c.get("status") == "passed" and c.get("model") == verified["model"]
                    and c.get("snapshot_id") == done.get("snapshot_id")
                    and c.get("review_version_ids") == done.get("review_version_ids")), None)
                if not started:
                    continue
                snap = tracking._snapshot(state, done["snapshot_id"])
                saved_snap = tracking._snapshot(runtime, done["snapshot_id"])
                if snap["source_hash"] != saved_snap["source_hash"]:
                    continue
                for row in snap["reviews"].values():
                    key = tracking._cache_key(row)
                    if row["review_version_id"] in done["review_version_ids"] and key not in verified["cache"] and key in runtime["cache"]:
                        verified["cache"][key] = tracking._valid_extraction(row["review_id"], row["text"], runtime["cache"][key])
            files.append(runtime_path)
    source = dict(label="Dinner Date 已核验的 56 问题目录及逐条分析来源",
        files=[dict(path=str(p.relative_to(root)), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files])
    return tracking.prepare_reanalysis(state, verified, source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true", help="Save offline reused analysis as tracking derived data")
    parser.add_argument("--analyze", action="store_true", help="Call existing models only after explicit approval")
    args = parser.parse_args()
    state = prepare()
    print(json.dumps({"before": [tracking._stats(state, s)[0] for s in state["snapshots"]]}, ensure_ascii=True))
    if args.analyze:
        import app
        app.load_env()
        tracking.complete_reanalysis(state)
    if args.save:
        tracking.save_reanalysis(ROOT, state)
        print(str(tracking.reanalysis_path(ROOT, state["game"])))
    print(json.dumps(tracking.catalog(state), ensure_ascii=True))
