"""Audit the two canonical Dinner Date snapshots; never run game-update analysis."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import data_flow_v2 as flow

SOURCES = (ROOT / "data/steam-94000-history-20250901.json",
           ROOT / "data/steam-94000-current-20260911.json")


def main():
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in SOURCES]
    dataset = flow.load_snapshots(snapshots)
    changes, conflicts = flow.review_history(dataset)
    ids = [{row["review_id"] for row in snapshot["reviews"]} for snapshot in snapshots]
    counts = Counter(row["change_type"] for row in changes)
    counts.update(history_only=len(ids[0] - ids[1]), current_only=len(ids[1] - ids[0]))
    assert dataset["app_id"] == 94000 and not conflicts
    assert len(ids[0]) == 100 and len(ids[1]) == 830 and len(ids[0] & ids[1]) == 100
    assert counts["review_revision"] == 0 and counts["source_text_changed"] == 9
    result = dict(experiment="dinner-date-snapshot-audit-v3", created_at=datetime.now(timezone.utc).isoformat(),
                  source_files=[str(path.relative_to(ROOT)) for path in SOURCES],
                  source_hashes=[hashlib.sha256(path.read_bytes()).hexdigest() for path in SOURCES],
                  game_update_analysis_performed=False,
                  scope="Canonical Dinner Date snapshots only; review revisions never become game-update cohorts.",
                  counts=dict(counts), conflicting_review_ids=conflicts, changes=changes)
    folder = ROOT / "evaluation/dinner-date-snapshot-audit"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(output=str(destination), counts=dict(counts)), ensure_ascii=True))


if __name__ == "__main__":
    main()
