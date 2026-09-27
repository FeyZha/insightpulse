"""Dinner Date data-only alignment. No imports of app, pipeline or AI clients.

python scripts/align_external_snapshots.py <run-folder> --collect
Omit --collect to rebuild a new alignment report from saved raw responses only.
"""
from collections import Counter
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

APP_ID = 94000
PARAMS = dict(json=1, num_per_page=100, language="all", review_type="all",
              purchase_type="all", filter="recent", day_range=365, filter_offtopic_activity=0)
API = "https://store.steampowered.com/appreviews/94000"


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, obj):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)


def epoch(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Timestamp must be positive integer epoch seconds")
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def normalize(raw, source_type, provenance, fetched_at=None):
    rid = raw.get("recommendationid")
    if type(rid) not in (str, int) or not str(rid).isdigit() or int(rid) <= 0:
        raise ValueError("Missing or invalid review ID")
    if "appid" in raw and raw["appid"] != APP_ID:
        raise ValueError("Wrong game")
    text = raw.get("review")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty or invalid text")
    created, updated = epoch(raw.get("timestamp_created")), epoch(raw.get("timestamp_updated"))
    if raw["timestamp_updated"] < raw["timestamp_created"]:
        raise ValueError("Updated before created")
    if fetched_at and updated > fetched_at:
        raise ValueError("Updated after capture")
    if type(raw.get("voted_up")) is not bool or not isinstance(raw.get("language"), str) or not raw["language"]:
        raise ValueError("Invalid recommendation/language")
    return dict(app_id=APP_ID, review_id=str(rid), text=text, created_at=created, updated_at=updated,
                language=raw["language"], recommended=raw["voted_up"], _raw=raw, _provenance=provenance)


def normalized_snapshot(entries, source_type):
    rows, audit, rejected, occurrences, duplicates, conflicts = {}, {}, [], Counter(), {}, set()
    for index, (raw, provenance, fetched_at) in enumerate(entries):
        rid = str(raw.get("recommendationid", "")) if isinstance(raw, dict) else ""
        if rid.isdigit():
            occurrences[rid] += 1
        try:
            row = normalize(raw, source_type, provenance, fetched_at)
            rid = row["review_id"]
            if rid in rows:
                duplicates.setdefault(rid, []).append(provenance)
                # Never silently overwrite a changing record during pagination.
                if row["_raw"] != audit[rid]["raw"]:
                    conflicts.add(rid)
            else:
                audit[rid] = dict(raw=row.pop("_raw"), provenance=row.pop("_provenance"))
                rows[rid] = row
        except (ValueError, TypeError, AttributeError, OverflowError, OSError) as exc:
            rejected.append(dict(index=index, review_id=rid, reason=str(exc), provenance=provenance))
    for rid in conflicts:
        rows.pop(rid, None)
        audit.pop(rid, None)
    return dict(app_id=APP_ID, source_type=source_type, input_count=len(entries), count=len(rows),
                reviews=sorted(rows.values(), key=lambda r: int(r["review_id"])), rejected=rejected,
                duplicate_review_ids={k: v for k, v in occurrences.items() if v > 1},
                duplicate_extra_rows=sum(n-1 for n in occurrences.values() if n > 1),
                duplicate_provenance=duplicates, conflicting_duplicate_ids=sorted(conflicts), _audit=audit)


def flatten(obj, prefix=""):
    result = {}
    for key, value in obj.items():
        path = prefix + key
        if isinstance(value, dict):
            result.update(flatten(value, path + "."))
        else:
            result[path] = value
    return result


def align(history, current):
    before = {r["review_id"]: r for r in history["reviews"]}
    after = {r["review_id"]: r for r in current["reviews"]}
    aligned = []
    counts = Counter({k: 0 for k in ("overlap", "snapshot_unchanged", "review_revision", "source_text_changed",
                                    "metadata_changed", "history_only", "current_only")})
    for rid in sorted(set(before) | set(after), key=int):
        b, a = before.get(rid), after.get(rid)
        raw_equal = normalized_equal = text_changed = None
        changes, added, removed = {}, [], []
        if b is None:
            category, reason = "current_only", "Current snapshot only; historical archive may be incomplete, not new_review"
        elif a is None:
            category, reason = "history_only", "Not observed in this current capture; not proof of deletion or resolution"
        else:
            counts["overlap"] += 1
            raw_equal = b["text"] == a["text"]
            normalized_equal = " ".join(b["text"].split()) == " ".join(a["text"].split())
            text_changed = not raw_equal
            bm = flatten(history["_audit"][rid]["raw"])
            am = flatten(current["_audit"][rid]["raw"])
            ignore = {"recommendationid", "review", "appid"}
            changes = {k: dict(history=bm[k], current=am[k]) for k in sorted((bm.keys() & am.keys()) - ignore) if bm[k] != am[k]}
            added, removed = sorted(am.keys() - bm.keys() - ignore), sorted(bm.keys() - am.keys() - ignore)
            if text_changed and a["updated_at"] > b["updated_at"]:
                category, reason = "review_revision", "Same review ID; text and source updated_at both changed"
            elif text_changed:
                category, reason = "source_text_changed", "Same review ID and updated_at; text difference is not a confirmed player edit"
            elif changes:
                category, reason = "metadata_changed", "Same review ID and text; shared metadata changed"
            else:
                category, reason = "snapshot_unchanged", "Same review ID, text and shared metadata"
        counts[category] += 1
        aligned.append(dict(review_id=rid, category=category, reason=reason,
                            raw_text_equal=raw_equal, normalized_text_equal=normalized_equal, text_changed=text_changed,
                            differences_only_at_heart_characters=bool(text_changed and len(b["text"]) == len(a["text"])
                                and all(x == y or y == "♥" for x, y in zip(b["text"], a["text"]))),
                            metadata_changes=changes, metadata_fields_added=added, metadata_fields_removed=removed,
                            identity_conflict=bool(b and a and b["created_at"] != a["created_at"]),
                            history=b, current=a))
    return dict(counts=dict(counts), rows=aligned,
                overlap_rate=counts["overlap"]/len(before) if before else None,
                historical_coverage=counts["overlap"]/len(before) if before else None,
                current_match_rate=counts["overlap"]/len(after) if after else None,
                normalized_text_rule="Collapse Unicode whitespace runs to a space and trim; raw text preserved",
                metadata_rule="Compare shared original metadata leaf values, including author/votes; added/removed schema fields separately recorded; capture provenance excluded")


def collect(folder):
    pages = folder / "steam-pages"
    pages.mkdir()  # A repeated collection must use a new directory.
    cursor, seen, logs, page_records = "*", set(), [], []
    started, complete, error = utc(), False, None
    try:
        while True:
            if cursor in seen:
                raise ValueError("Repeated cursor: completeness cannot be established")
            seen.add(cursor)
            page = len(page_records) + 1
            params = dict(PARAMS, cursor=cursor)
            url = API + "?" + urlencode(params)
            result = None
            for attempt in range(1, 4):
                log = dict(page=page, attempt=attempt, requested_at=utc(), request_params=params, url=url, cursor_in=cursor)
                raw, retryable = None, True
                try:
                    with urlopen(Request(url, headers={"User-Agent": "InsightPulse/0.1 data alignment study"}), timeout=40) as response:
                        raw = response.read()
                        log.update(http_status=response.status, headers=dict(response.headers))
                    log["fetched_at"] = utc()
                    result = json.loads(raw)
                    if result.get("success") != 1 or not isinstance(result.get("reviews"), list):
                        raise ValueError("Invalid Steam review response")
                    if result["reviews"] and not isinstance(result.get("cursor"), str):
                        raise ValueError("Missing next cursor")
                    log.update(status="success", cursor_out=result.get("cursor"), num_reviews=len(result["reviews"]))
                except HTTPError as exc:
                    raw = exc.read()
                    log.update(status="failed", error=str(exc), http_status=exc.code)
                    retryable = exc.code == 429 or exc.code >= 500
                except (URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError) as exc:
                    log.update(status="failed", error=str(exc))
                finally:
                    log["finished_at"] = utc()
                    if raw is not None:
                        name = f"page-{page:04d}-attempt-{attempt}.raw"
                        (pages / name).write_bytes(raw)
                        log.update(raw_file="steam-pages/" + name, response_hash=sha(raw))
                    save(pages / f"page-{page:04d}-attempt-{attempt}.request.json", log)
                    logs.append(log)
                if log["status"] == "success":
                    break
                if not retryable or attempt == 3:
                    raise ValueError("Steam page failed; raw attempts preserved")
                time.sleep(attempt)
            page_records.append(log)
            print(f"page={page} rows={len(result['reviews'])}", flush=True)
            if not result["reviews"]:
                complete = True
                break
            cursor = result["cursor"]
            time.sleep(.35)
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        save(folder / "collection.json", dict(source_type="steam_api", app_id=APP_ID, source_url=API,
             started_at=started, fetched_at=utc(), request_params=PARAMS, page_count=len(page_records),
             attempts=len(logs), request_failures=sum(x["status"] != "success" for x in logs),
             retries=sum(x["attempt"] > 1 for x in logs), cursor_exhausted=complete, error=error, pages=page_records,
             scope="All reviews reachable by these parameters until an empty page; not an atomic snapshot",
             day_range_note="Steam docs: day_range applies only to filter=all, max 365; recent cursor traversal is not day-limited"))


def stats(snapshot):
    rows = snapshot["reviews"]
    return dict(raw_count=snapshot["input_count"], valid_unique_count=len(rows),
                created_at_range=[min(r["created_at"] for r in rows), max(r["created_at"] for r in rows)] if rows else None,
                updated_at_range=[min(r["updated_at"] for r in rows), max(r["updated_at"] for r in rows)] if rows else None,
                languages=dict(Counter(r["language"] for r in rows)),
                recommended=sum(r["recommended"] for r in rows), not_recommended=sum(not r["recommended"] for r in rows),
                rejected_records=len(snapshot["rejected"]), duplicate_id_count=len(snapshot["duplicate_review_ids"]),
                duplicate_extra_rows=snapshot["duplicate_extra_rows"], conflicting_duplicate_ids=snapshot["conflicting_duplicate_ids"])


def export_snapshot(snapshot):
    source_hash = (snapshot.get("source_provenance") or {}).get("sha256")
    if source_hash is None:
        source_hash = sha("".join(snapshot.get("response_hash", [])).encode())
    sid = sha(json.dumps([APP_ID, snapshot["observed_at"], snapshot["source_type"], source_hash]).encode())[:24]
    meta = dict(snapshot_id=sid, app_id=APP_ID, observed_at=snapshot["observed_at"],
                observed_at_precision=snapshot["observed_at_precision"], source_type=snapshot["source_type"],
                source_url=(snapshot.get("source_provenance") or {}).get("url", snapshot.get("source_url")),
                source_hash=source_hash,
                coverage="exhausted" if snapshot["source_type"] == "steam_api" else "bounded_archive")
    return dict(snapshot=meta, reviews=[dict(snapshot_id=sid, **row) for row in snapshot["reviews"]])


def analyze(folder):
    source = json.loads((folder / "archive-source.json").read_text(encoding="utf-8"))
    compressed = (folder / "historical-original.json.gz").read_bytes()
    if sha(compressed) != source["sha256"]:
        raise ValueError("Archive checksum changed")
    archive = json.loads(gzip.decompress(compressed))
    groups = [g for g in archive["reviews"] if str(g["appid"]) == str(APP_ID)]
    history_entries = []
    for group_index, group in enumerate(groups):
        if group["review_data"].get("success") != 1:
            raise ValueError("Historical source response was unsuccessful")
        for row_index, row in enumerate(group["review_data"]["reviews"]):
            history_entries.append((row, dict(source, appid=APP_ID, target_group_index=group_index,
                row_index=row_index, archive_metadata=archive["metadata"]), None))
    current_entries = []
    capture = json.loads((folder / "collection.json").read_text(encoding="utf-8"))
    summaries = []
    for page in capture["pages"]:
        raw = (folder / page["raw_file"]).read_bytes()
        if sha(raw) != page["response_hash"]:
            raise ValueError("Steam response checksum changed")
        obj = json.loads(raw)
        if "query_summary" in obj:
            summaries.append(obj["query_summary"])
        for index, row in enumerate(obj["reviews"]):
            current_entries.append((row, dict(raw_file=page["raw_file"], response_hash=page["response_hash"],
                source_url=page["url"], request_params=page["request_params"], page=page["page"], row_index=index,
                page_count=capture["page_count"]), page["fetched_at"]))
    history = normalized_snapshot(history_entries, "github_archive")
    current = normalized_snapshot(current_entries, "steam_api")
    history.update(observed_at="2025-09-01T23:59:59+00:00", observed_at_precision="day",
                   source_provenance=source, archive_metadata=archive["metadata"],
                   archive_query_summaries=[g["review_data"]["query_summary"] for g in groups])
    current.update(observed_at=capture["fetched_at"], observed_at_precision="instant",
                   source_url=capture["source_url"])
    current["response_hash"] = [p["response_hash"] for p in capture["pages"]]
    result = align(history, current)
    result.update(history_stats=stats(history), current_stats=stats(current), collection=capture,
                  current_query_summaries=summaries, source=source, archive_metadata=archive["metadata"],
                  review_revision_ids=[r["review_id"] for r in result["rows"] if r["category"] == "review_revision" and not r["identity_conflict"]],
                  source_text_change_ids=[r["review_id"] for r in result["rows"] if r["category"] == "source_text_changed" and not r["identity_conflict"]],
                  unexplained_text_diff_candidates=[r["review_id"] for r in result["rows"] if r["text_changed"]
                      and not r["differences_only_at_heart_characters"] and not r["identity_conflict"]],
                  identity_conflicts=[r["review_id"] for r in result["rows"] if r["identity_conflict"]])
    cases = {}
    for category in ("snapshot_unchanged", "review_revision", "source_text_changed", "metadata_changed",
                     "history_only", "current_only"):
        matches = [r for r in result["rows"] if r["category"] == category]
        cases[category] = matches[:3]
    out = folder / ("alignment-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    out.mkdir()
    save(out / "history-normalized.json", export_snapshot(history))
    save(out / "current-normalized.json", export_snapshot(current))
    save(out / "alignment.json", result)
    save(out / "cases.json", cases)
    save(out / "checksums.json", {str(p.relative_to(folder)): sha(p.read_bytes()) for p in folder.rglob("*") if p.is_file()})
    print(json.dumps(dict(output=str(out), counts=result["counts"], history=result["history_stats"], current=result["current_stats"],
                          overlap_rate=result["overlap_rate"], current_match_rate=result["current_match_rate"]), ensure_ascii=True))
    return out, result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    if args.collect:
        collect(args.folder)
    analyze(args.folder)
