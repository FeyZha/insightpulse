"""Capture a bounded, auditable Steam sample. Run: python scripts/collect_sample.py"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review_translation as translation

APP_ID = 94000


def fetch(url):
    request = Request(url, headers={"User-Agent": "InsightPulse/0.1 data feasibility study"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def normalize(review):
    review_id = str(review["recommendationid"])
    if not review_id.isdigit():
        raise ValueError("Invalid Steam identifiers")
    return {
        "review_id": review_id,
        "text": review["review"],
        "created_at": int(review["timestamp_created"]),
        "updated_at": int(review["timestamp_updated"]),
        "language": review["language"],
        "recommended": review["voted_up"],
    }


def collect(app_id, pages, since=None):
    if app_id not in (APP_ID, 1022980) or not 1 <= pages <= 100:
        raise ValueError("Only configured games (94000, 1022980) are accepted; pages must be 1–100")
    started = datetime.now(timezone.utc).isoformat()
    filters = {"filter": "recent", "language": "all", "purchase_type": "all", "review_type": "all",
               "filter_offtopic_activity": 0}
    reviews, sources, seen_cursors = {}, [], set()
    cursor = "*"
    exhausted = False
    for _ in range(pages):
        if cursor in seen_cursors:
            raise ValueError("Steam repeated a pagination cursor; coverage cannot be trusted")
        seen_cursors.add(cursor)
        query = urlencode({"json": 1, **filters,
                           "num_per_page": 100, "cursor": cursor})
        url = f"https://store.steampowered.com/appreviews/{app_id}?{query}"
        result = fetch(url)
        if result.get("success") != 1:
            raise ValueError("Steam review request did not succeed")
        sources.append(url)
        batch = result.get("reviews", [])
        for raw in batch:
            item = normalize(raw)
            if item["review_id"] in reviews and reviews[item["review_id"]] != item:
                raise ValueError(f"Steam returned conflicting states for review {item['review_id']}; snapshot discarded")
            reviews[item["review_id"]] = item
        if since is not None and batch and min(int(r["timestamp_created"]) for r in batch) < since:
            break
        if not batch:
            exhausted = True
            break
        cursor = result["cursor"]
        time.sleep(0.35)
    query = urlencode({"appid": app_id, "count": 20, "maxlength": 0,
                       "feeds": "steam_community_announcements"})
    news_url = f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?{query}"
    news = fetch(news_url)["appnews"]["newsitems"]
    items = sorted(reviews.values(), key=lambda r: r["created_at"])
    observed = datetime.now(timezone.utc).isoformat()
    sid = hashlib.sha256(json.dumps([app_id, observed, items], sort_keys=True).encode()).hexdigest()[:24]
    return {
        "snapshot": {"snapshot_id": sid, "app_id": app_id, "observed_at": observed,
                     "collection_started_at": started, "collection_finished_at": observed,
                     "filters": dict(filters, **({"stop_before_created": since} if since is not None else {})),
                     "tracking_capture_version": 1,
                     "observed_at_precision": "instant", "source_type": "steam_api",
                     "source_url": f"https://store.steampowered.com/appreviews/{app_id}",
                     "coverage": "exhausted" if exhausted else "bounded_recent_sample"},
        "reviews": [dict(snapshot_id=sid, app_id=app_id, **row) for row in items],
        "collection": {"max_pages": pages, "count": len(items),
                       "edited_count": sum(r["updated_at"] > r["created_at"] for r in items),
                       "earliest_created": items[0]["created_at"] if items else None,
                       "latest_created": items[-1]["created_at"] if items else None,
                       "sources": sources + [news_url], "announcements": news}
    }


def save_capture(root, snapshot, progress=lambda message: None, *, translate=True):
    folder = Path(root) / "data"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = folder / f"steam-{snapshot['snapshot']['app_id']}-{stamp}.json"
    # Preserve the original capture even if translation fails or needs a later retry.
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2)
    if not translate:
        return destination
    try:
        result = translation.translate(root, snapshot["reviews"], progress=progress)
    except (ValueError, OSError):
        raise ValueError(f"原文已保存为 {destination.name}，翻译未完成；请检查模型配置后补译，无需重新采集") from None
    if result["failed"]:
        raise ValueError(f"原文已保存为 {destination.name}，{result['failed']} 条评论翻译未完成；成功译文已保留，可补译，无需重新采集")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", type=int, default=APP_ID)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--since", help="Stop after crossing this UTC date (YYYY-MM-DD)")
    args = parser.parse_args()
    from app import load_env
    load_env()
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() if args.since else None
    data = collect(args.app_id, args.pages, since)
    destination = save_capture(Path(__file__).resolve().parents[1], data)
    print(json.dumps({"path": str(destination), "reviews": data["collection"]["count"],
                      "edited": data["collection"]["edited_count"],
                      "announcements": len(data["collection"]["announcements"]),
                      "coverage": data["snapshot"]["coverage"]}, ensure_ascii=True))
