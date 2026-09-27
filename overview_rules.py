"""Deterministic overview of saved review analysis; never calls a model."""
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import json

import data_flow_v2 as flow
import pipeline

RULE_VERSION = "global-overview-v1"
CATEGORIES = tuple(category for category in flow.CATEGORIES if category != "none")


def evidence_order(item):
    try:
        updated = flow.timestamp(item.get("updated_at"))
    except (ValueError, TypeError, OverflowError):
        updated = float("-inf")
    return (-updated, str(item.get("review_id", "")), str(item.get("observation_id", "")))


def claim_kind(observation, text):
    claim, quote = observation.get("claim"), observation.get("change_evidence")
    if claim == "complaint":
        return claim
    if claim in ("improved", "worsened") and isinstance(quote, str) and quote.strip() and quote in text:
        return claim
    return "unverified"


def _validate(categories, observations, text):
    # Keep usable observations with unverified change claims, but never count a change as fact.
    cleaned = [dict(item, claim="complaint", change_evidence="")
               if claim_kind(item, text) == "unverified" else item for item in observations]
    return flow.validate_observations(json.dumps({"reviews": [{
        "id": "review", "categories": categories, "observations": cleaned,
    }]}), [{"id": "review", "text": text}])["review"]


def analysis_inputs(raw, source, records):
    """Use current source text and saved validation results, never infer none from absence."""
    reviews = {}
    for row in source.get("reviews", []):
        rid = str(row.get("review_id", row.get("id", "")))
        if rid and (rid not in reviews or evidence_order(row) < evidence_order(reviews[rid])):
            reviews[rid] = row
    by_review = {}
    for observation in raw.get("observations", []):
        by_review.setdefault(str(observation.get("review_id", "")), []).append(observation)
    statuses, valid = {}, {}
    for rid, review in reviews.items():
        text = review.get("text")
        if (not isinstance(text, str) or not text.strip() or len(text) > 12000
                or review.get("app_id", source.get("snapshot", {}).get("app_id"))
                != source.get("snapshot", {}).get("app_id", review.get("app_id"))):
            statuses[rid] = "excluded"
            continue
        record = records.get(rid)
        if record and record.get("text") != text:
            statuses[rid] = "pending"
            continue
        if record and record.get("failure"):
            statuses[rid] = "excluded" if record["failure"] == "empty_or_overlong_text" else "failed"
            continue
        rows = by_review.get(rid, [])
        if not record and not rows:
            statuses[rid] = "pending"
            continue
        try:
            if record:
                parsed = _validate(record.get("categories"), record.get("observations"), text)
                if bool(parsed["observations"]) != bool(rows):
                    raise ValueError("Saved extraction and observation export disagree")
            seen = {}
            for observation in rows:
                if (observation.get("review_text", text) != text or not observation.get("observation_id")
                        or (review.get("review_version_id") and observation.get("review_version_id")
                            and review["review_version_id"] != observation["review_version_id"])):
                    raise ValueError("Observation belongs to another text/version")
                parsed = _validate([{"category": observation.get("category"), "confidence": 1}],
                                   [observation], text)
                row = dict(observation, evidence=parsed["observations"][0]["evidence"])
                row["review_id"] = rid
                seen[row["observation_id"]] = row
            valid.update(seen)
            statuses[rid] = "with_feedback" if seen else "without_feedback"
        except (ValueError, TypeError, KeyError, IndexError):
            statuses[rid] = "failed"
    return reviews, statuses, valid


def build(raw, source, issues, inputs, category_names):
    reviews, statuses, observations = inputs
    dates = []
    for review in reviews.values():
        try:
            dates.append(datetime.fromtimestamp(flow.timestamp(review.get("created_at")), timezone.utc).date().isoformat())
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    months, years = Counter(day[:7] for day in dates), Counter(day[:4] for day in dates)
    timeline = dict(months=[], years=[], dated=len(dates), undated=len(reviews) - len(dates),
                    start=min(dates) if dates else None, end=max(dates) if dates else None)
    if dates:
        first_year, first_month = map(int, timeline["start"][:7].split("-"))
        last_year, last_month = map(int, timeline["end"][:7].split("-"))
        timeline["months"] = [dict(period=period, reviews=months[period])
                              for index in range(first_year * 12 + first_month - 1, last_year * 12 + last_month)
                              for period in [f"{index // 12:04d}-{index % 12 + 1:02d}"]]
        timeline["years"] = [dict(period=f"{year:04d}", reviews=years[f"{year:04d}"])
                             for year in range(first_year, last_year + 1)]
    counts = Counter(statuses.values())
    analyzed = counts["with_feedback"] + counts["without_feedback"]
    feedback = counts["with_feedback"]
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    category_reviews = {key: set() for key in CATEGORIES}
    category_observations = Counter()
    review_categories = {}
    for observation in observations.values():
        key, rid = observation["category"], observation["review_id"]
        category_reviews[key].add(rid)
        category_observations[key] += 1
        review_categories.setdefault(rid, set()).add(key)
    global_issues = []
    raw_issues = {item["issue_id"]: item for item in raw.get("issues", [])}
    for issue in issues:
        members = [observations[oid] for oid in dict.fromkeys(raw_issues[issue["id"]]["observation_ids"])
                   if oid in observations and observations[oid]["category"] == issue["category"]]
        ids = sorted({row["review_id"] for row in members})
        if not ids:
            continue
        claims = {claim: set() for claim in ("complaint", "worsened", "improved", "unverified")}
        for row in members:
            claims[claim_kind(row, reviews[row["review_id"]]["text"])].add(row["review_id"])
        summary = {key: value for key, value in issue.items()
                   if key not in ("evidence", "evidence_items", "player_themes")}
        global_issues.append(dict(summary, review_ids=ids, reviews=len(ids), observations=len(members),
                                  claim_counts=dict(Counter(row.get("claim", "unknown") for row in members)),
                                  claim_review_counts={key: len(value) for key, value in claims.items()}))
    global_issues.sort(key=lambda item: (-item["reviews"], item["id"]))
    issue_counts = Counter(issue["category"] for issue in global_issues)
    categories = [dict(key=key, label=category_names[key], observations=category_observations[key],
                       issues=issue_counts[key], reviews=len(category_reviews[key]),
                       review_ids=sorted(category_reviews[key]), share=ratio(len(category_reviews[key]), analyzed))
                  for key in CATEGORIES]
    categories.sort(key=lambda item: -item["reviews"])
    top = [item for item in categories if item["reviews"]][:3]
    top_ids = set().union(*(category_reviews[item["key"]] for item in top))
    pairs = []
    for a, b in combinations(CATEGORIES, 2):
        shared = category_reviews[a] & category_reviews[b]
        if len(shared) >= 5:
            pairs.append(dict(a=a, b=b, reviews=len(shared), review_ids=sorted(shared),
                              share_a=ratio(len(shared), len(category_reviews[a])),
                              share_b=ratio(len(shared), len(category_reviews[b]))))
    pairs.sort(key=lambda item: -item["reviews"])
    single = sum(len(keys) == 1 for keys in review_categories.values())
    multiple = sum(len(keys) > 1 for keys in review_categories.values())
    recommended = sum(row.get("recommended") is True for row in reviews.values())
    not_recommended = sum(row.get("recommended") is False for row in reviews.values())
    statements = [
        (f"{top[0]['label']}关联评论最多：{top[0]['reviews']} 条，占已分析评论的 {top[0]['share']:.1%}。"
         if top else "暂无具体反馈，无法计算类别排名。"),
        (f"{'、'.join(item['label'] for item in top)}共涉及 {len(top_ids)} 条评论，覆盖含具体反馈评论的 {len(top_ids) / feedback:.1%}。"
         if top and feedback else "暂无具体反馈，无法计算类别覆盖率。"),
        (f"跨类别评论 {multiple} 条，占含具体反馈评论的 {multiple / feedback:.1%}。"
         if feedback else "暂无具体反馈，无法计算跨类别占比。"),
    ]
    meta = source.get("snapshot", source)
    return dict(rule_version=RULE_VERSION,
                scope=dict(collected=len(reviews), analyzed=analyzed, with_feedback=feedback,
                           without_feedback=counts["without_feedback"], pending=counts["pending"],
                           failed=counts["failed"], excluded=counts["excluded"],
                           coverage=meta.get("coverage", "unknown"), observed_at=meta.get("observed_at", meta.get("captured_at"))),
                recommendation=dict(recommended=recommended, not_recommended=not_recommended,
                                    unknown=len(reviews) - recommended - not_recommended,
                                    rate=ratio(recommended, recommended + not_recommended)),
                timeline=timeline,
                categories=categories, issues=global_issues,
                top_categories=dict(keys=[item["key"] for item in top], reviews=len(top_ids), share=ratio(len(top_ids), feedback)),
                overlap=dict(single=single, multiple=multiple, share=ratio(multiple, feedback), pairs=pairs[:3]),
                statements=statements,
                versions=dict(snapshot=str(meta.get("snapshot_id", "unknown")),
                              analysis=pipeline.fingerprint([raw.get("processing_version"), raw.get("prompt_version"),
                                                             statuses, list(observations.values())]),
                              grouping=pipeline.fingerprint(raw.get("issues", [])), rules=RULE_VERSION))
