"""Offline pagination and failure checks: python scripts/test_collect_sample.py"""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import collect_sample as collector


def review(identifier):
    return {"recommendationid": identifier, "author": {"steamid": "123"},
            "review": "Example text", "timestamp_created": 10, "timestamp_updated": 12,
            "language": "english", "voted_up": True, "steam_purchase": True}


collector.time.sleep = lambda _: None
responses = iter([
    {"success": 1, "reviews": [review("1"), review("2")], "cursor": "next"},
    {"success": 1, "reviews": [review("2"), review("3")], "cursor": "last"},
    {"appnews": {"newsitems": []}},
])
collector.fetch = lambda _: next(responses)
before_collection = datetime.now(timezone.utc)
data = collector.collect(94000, 2)
after_collection = datetime.now(timezone.utc)
assert data["collection"]["count"] == 3 and data["collection"]["edited_count"] == 3
assert data["snapshot"]["coverage"] == "bounded_recent_sample"
assert set(data["reviews"][0]) == {"snapshot_id", "app_id", "review_id", "text", "created_at",
                                   "updated_at", "language", "recommended"}
assert all(r["snapshot_id"] == data["snapshot"]["snapshot_id"] for r in data["reviews"])
meta = data["snapshot"]
assert before_collection <= datetime.fromisoformat(meta["collection_started_at"]) <= \
    datetime.fromisoformat(meta["collection_finished_at"]) <= after_collection
assert meta["observed_at"] == meta["collection_finished_at"]
assert meta["tracking_capture_version"] == 1
assert meta["filters"] == {"filter": "recent", "language": "all", "purchase_type": "all", "review_type": "all",
                           "filter_offtopic_activity": 0}
for url in data["collection"]["sources"][:-1]:
    query = parse_qs(urlparse(url).query)
    assert query["filter"] == ["recent"]
    assert all(query[key] == [str(value)] for key, value in meta["filters"].items())

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    original = json.dumps(data, ensure_ascii=False, indent=2)
    messages = []
    def translated(root, items, progress):
        saved = list((root / "data").glob("steam-94000-*.json"))
        assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == original
        assert items == data["reviews"]
        progress("translation ready")
        return {"failed": 0}
    with patch.object(collector.translation, "translate", side_effect=translated) as translate:
        destination = collector.save_capture(root, data, progress=messages.append)
    translate.assert_called_once()
    assert messages == ["translation ready"]
    assert destination.read_text(encoding="utf-8") == original
    assert json.dumps(data, ensure_ascii=False, indent=2) == original

    # Both provider failures and missing credentials retain an immutable capture.
    for outcome in ({"failed": 2}, ValueError("No API Key"), OSError("Provider unavailable")):
        prior = {path: path.read_bytes() for path in (root / "data").glob("*.json")}
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with patch.object(collector.translation, "translate", **kwargs):
            try:
                collector.save_capture(root, data)
            except ValueError as exc:
                created = set((root / "data").glob("*.json")) - prior.keys()
                assert len(created) == 1
                saved = created.pop()
                assert saved.name in str(exc) and "补译" in str(exc)
                assert saved.read_text(encoding="utf-8") == original
            else:
                raise AssertionError("Incomplete translation was accepted")
        assert all(path.read_bytes() == content for path, content in prior.items())

# A caller's stopping boundary is part of the saved scope, not an implicit period.
responses = iter([{"success": 1, "reviews": [review("1")], "cursor": "next"},
                  {"appnews": {"newsitems": []}}])
bounded = collector.collect(94000, 2, since=11)
assert bounded["snapshot"]["filters"]["stop_before_created"] == 11
assert bounded["snapshot"]["coverage"] == "bounded_recent_sample"
import tracking
assert tracking._scope(bounded)["stop_before_created"] == "11"

# Neither pagination overlaps nor a duplicate inside one page may hide a changed state.
for field, value in (("review", "Edited text"), ("timestamp_created", 9), ("timestamp_updated", 13),
                     ("language", "schinese"), ("voted_up", False)):
    for same_page in (True, False):
        changed = dict(review("1"), **{field: value})
        batches = [[review("1"), changed]] if same_page else [[review("1")], [changed]]
        responses = iter({"success": 1, "reviews": rows, "cursor": str(i)}
                         for i, rows in enumerate(batches))
        try:
            collector.collect(94000, len(batches))
        except ValueError as exc:
            assert "conflicting states" in str(exc)
        else:
            raise AssertionError(f"Conflicting {field} state was accepted")

responses = iter([{"success": 1, "reviews": [], "cursor": "*"}, {"appnews": {"newsitems": []}}])
data = collector.collect(94000, 2)
assert data["collection"]["count"] == 0 and data["collection"]["earliest_created"] is None
assert data["snapshot"]["coverage"] == "exhausted"

responses = iter([{"success": 1, "reviews": [review("9")], "cursor": "end"},
                  {"success": 1, "reviews": [], "cursor": "end"}, {"appnews": {"newsitems": []}}])
collector.fetch = lambda _: next(responses)
second_game = collector.collect(1022980, 3)
assert second_game["snapshot"]["app_id"] == 1022980
assert second_game["snapshot"]["coverage"] == "exhausted"
with tempfile.TemporaryDirectory() as folder, patch.object(collector.translation, "translate", side_effect=AssertionError("model called")):
    path = collector.save_capture(folder, second_game, translate=False)
    assert json.loads(path.read_text(encoding="utf-8")) == second_game

collector.fetch = lambda _: {"success": 0}
try:
    collector.collect(94000, 1)
except ValueError:
    pass
else:
    raise AssertionError("API failure was silently accepted")

collector.fetch = lambda _: {"success": 1, "reviews": [review("1")], "cursor": "*"}
try:
    collector.collect(94000, 2)
except ValueError:
    pass
else:
    raise AssertionError("Repeated pagination cursor was accepted")

for app_id, pages in [(553850, 1), (94000, 0), (94000, 101)]:
    try:
        collector.collect(app_id, pages)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid bounds were accepted")
print("PASS: deduplication, conflicting states, interval/scope, capture before translation, retry safety, empty data, API errors, cursor, bounds")
