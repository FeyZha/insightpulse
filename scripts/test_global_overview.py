"""Run: python scripts/test_global_overview.py; saved data only, no paid calls."""
from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import overview_rules as rules


def observation(oid, rid="r1", category="controls_input", claim="complaint", change="", evidence="bad"):
    return dict(observation_id=oid, review_id=rid, category=category, object="game", symptom=oid,
                description="具体反馈", evidence=evidence, claim=claim, change_evidence=change)


def record(text="bad; better now", observations=None, failure=None):
    rows = observations or []
    return dict(text=text, failure=failure, observations=rows,
                categories=[dict(category=key, confidence=1) for key in
                            dict.fromkeys(row["category"] for row in rows)] or [dict(category="none", confidence=1)])


def build(source, observations, records):
    raw_issues = [dict(issue_id=f"issue-{row['observation_id']}", category=row["category"],
                       observation_ids=[row["observation_id"]] * 2) for row in observations]
    # Deliberately wrong cached counts prove that global counts come from validated observations.
    issues = [dict(id=row["issue_id"], title=row["issue_id"], category=row["category"],
                   reviews=999, review_ids=["outside-snapshot"], claim_counts={"complaint": 999}) for row in raw_issues]
    raw = dict(observations=observations, issues=raw_issues)
    inputs = rules.analysis_inputs(raw, source, records)
    return rules.build(raw, source, issues, inputs, app.CATEGORY_NAMES), inputs


# More than 100 issues, deduped source IDs, mixed claims and accurate status partitions.
rows = [observation(f"o{i:03}", rid=f"bulk{i:03}") for i in range(105)]
rows += [observation("complaint"), observation("visual", category="visual_graphics"), observation("story", category="content_narrative"),
         observation("change", claim="improved", change="better now"),
         observation("unverified", claim="worsened", change="invented words"),
         observation("other-review", rid="r7"), observation("invalid", rid="r8", evidence="not in source")]
source = {"snapshot": {"coverage": "bounded_recent_sample"}, "reviews": [
    dict(review_id=rid, text="bad; better now", recommended=(True if rid == "r1" else None))
    for rid in ("r1", "r2", "r3", "r4", "r6", "r7", "r8")
] + [dict(review_id=f"bulk{i:03}", text="bad") for i in range(105)]
    + [dict(review_id="r5", text=""), dict(review_id="r1", text="bad; better now", recommended=True)]}
records = {"r1": record(observations=rows[105:110]), "r2": record(), "r3": record(failure="request failed"),
           "r6": record(text="old text")}
result, inputs = build(source, rows, records)
assert result["scope"] == dict(collected=113, analyzed=108, with_feedback=107, without_feedback=1,
                               pending=2, failed=2, excluded=1, coverage="bounded_recent_sample", observed_at=None)
assert len(result["categories"]) == 13 and len(result["issues"]) == 111
assert result["top_categories"]["reviews"] == 107 and result["top_categories"]["share"] == 1
assert result["overlap"] == dict(single=106, multiple=1, share=1/107, pairs=[])
assert result["recommendation"] == dict(recommended=1, not_recommended=0, unknown=112, rate=1)
assert all(row["reviews"] == 1 for row in result["issues"])
assert [row["id"] for row in result["issues"]] == sorted(row["id"] for row in result["issues"])
by_id = {row["id"]: row for row in result["issues"]}
assert by_id["issue-change"]["claim_review_counts"] == dict(complaint=0, worsened=0, improved=1, unverified=0)
assert by_id["issue-unverified"]["claim_review_counts"] == dict(complaint=0, worsened=0, improved=0, unverified=1)

# Deduplicate by (review, claim), while keeping independent claims on the same review.
mixed_rows = [observation("c1"), observation("c2"), observation("i", claim="improved", change="better now"),
              observation("w", claim="worsened", change=""), observation("w2", claim="worsened", change="bad")]
mixed_raw = dict(observations=mixed_rows, issues=[dict(issue_id="mixed", category="controls_input",
                                                     observation_ids=[row["observation_id"] for row in mixed_rows])])
mixed_source = dict(reviews=[dict(review_id="r1", text="bad; better now")])
mixed = rules.build(mixed_raw, mixed_source, [dict(id="mixed", category="controls_input")],
                    rules.analysis_inputs(mixed_raw, mixed_source, {}), app.CATEGORY_NAMES)
assert mixed["issues"][0]["claim_review_counts"] == dict(complaint=1, worsened=1, improved=1, unverified=1)
assert mixed["issues"][0]["reviews"] == 1

# Inclusive top pairs count a three-category review in all of its pairs; canonical order breaks ties.
three_rows = [observation(f"{rid}-{key}", rid=rid, category=key)
              for rid in map(str, range(5)) for key in ("controls_input", "visual_graphics", "content_narrative")]
three_source = dict(reviews=[dict(review_id=str(i), text="bad") for i in range(5)])
three, _ = build(three_source, three_rows, {})
assert [pair["reviews"] for pair in three["overlap"]["pairs"]] == [5, 5, 5]
assert three["overlap"]["pairs"][0]["a"] == "controls_input"
assert three["overlap"]["pairs"][0]["b"] == "visual_graphics"
assert three["top_categories"]["reviews"] == 5 and three["overlap"]["multiple"] == 5

# Publication dates use all deduped collected reviews, including pending/excluded text.
# UTC conversion crosses month/year boundaries; empty periods stay visible.
timeline_source = dict(snapshot=dict(observed_at="2026-09-21T00:00:00Z"), reviews=[
    dict(review_id="first", text="bad", created_at="2023-12-31T23:30:00-01:00"),
    dict(review_id="duplicate", text="bad", created_at="2024-01-15T00:00:00Z", updated_at="2026-09-03T00:00:00Z"),
    dict(review_id="duplicate", text="old", created_at="2021-01-01T00:00:00Z", updated_at="2026-09-01T00:00:00Z"),
    dict(review_id="epoch", text="bad", created_at=1706745600),
    dict(review_id="offset", text="bad", created_at="2024-03-01T00:30:00+01:00"),
    dict(review_id="excluded", text="", created_at="2026-03-01T00:00:00Z"),
    dict(review_id="missing", text="bad", updated_at="2026-09-02T00:00:00Z"),
] + [dict(review_id=f"invalid-{index}", text="bad", created_at=value)
     for index, value in enumerate((None, "2024-02-30T00:00:00Z", "2024-04-01T12:00:00",
                                    True, float("nan"), float("inf"), 1e300, {}, ""))])
dated, _ = build(timeline_source, [], {})
timeline = dated["timeline"]
assert timeline["dated"] == 5 and timeline["undated"] == 10
assert timeline["start"] == "2024-01-01" and timeline["end"] == "2026-03-01"
assert len(timeline["months"]) == 27
assert timeline["months"][:3] == [dict(period="2024-01", reviews=2), dict(period="2024-02", reviews=2),
                                    dict(period="2024-03", reviews=0)]
assert timeline["months"][-1] == dict(period="2026-03", reviews=1)
assert timeline["years"] == [dict(period="2024", reviews=4), dict(period="2025", reviews=0),
                              dict(period="2026", reviews=1)]
assert sum(row["reviews"] for row in timeline["months"]) == sum(row["reviews"] for row in timeline["years"]) == timeline["dated"]
assert timeline["dated"] + timeline["undated"] == dated["scope"]["collected"]
epoch, _ = build(dict(reviews=[dict(review_id="zero", text="bad", created_at=0)]), [], {})
assert epoch["timeline"] == dict(months=[dict(period="1970-01", reviews=1)], years=[dict(period="1970", reviews=1)],
                                  dated=1, undated=0, start="1970-01-01", end="1970-01-01")

# Empty input and missing source are unknown, never pretend unprocessed reviews have no feedback.
for missing_source in ({}, {"reviews": []}):
    empty, _ = build(missing_source, rows, {})
    assert empty["scope"]["analyzed"] == empty["scope"]["without_feedback"] == 0
    assert len(empty["categories"]) == 13 and empty["issues"] == []
    assert all(item["share"] is None for item in empty["categories"])
    assert empty["recommendation"]["rate"] is None and empty["overlap"]["share"] is None
    assert empty["timeline"] == dict(months=[], years=[], dated=0, undated=0, start=None, end=None)
none_source = dict(reviews=[dict(review_id="none", text="fine")])
none, _ = build(none_source, [], {"none": record(text="fine")})
assert none["scope"]["analyzed"] == none["scope"]["without_feedback"] == 1
assert none["top_categories"]["share"] is None and none["recommendation"]["unknown"] == 1
assert none["timeline"] == dict(months=[], years=[], dated=0, undated=1, start=None, end=None)
stale_source = dict(reviews=[dict(review_id="r1", text="bad", review_version_id="new")])
stale, _ = build(stale_source, [dict(observation("old"), review_version_id="old")], {})
assert stale["scope"]["analyzed"] == 0 and stale["scope"]["failed"] == 1

# Out-of-order duplicate source rows use the current version's metadata and translation context.
# A failed review in an otherwise valid Issue cannot inflate its detail or global counts.
with tempfile.TemporaryDirectory() as directory:
    source_path, run_path = Path(directory) / "source.json", Path(directory) / "run.json"
    source_path.write_text(json.dumps(dict(reviews=[
        dict(review_id="r1", text="bad", language="english", recommended=True, updated_at="2026-09-03T00:00:00Z"),
        dict(review_id="r1", text="old", language="russian", recommended=False, updated_at="2026-09-01T00:00:00Z"),
        dict(review_id="r2", text="bad", updated_at="2026-09-02T00:00:00Z"),
    ])), encoding="utf-8", newline="\n")
    run_path.write_text(json.dumps(dict(source=dict(file=str(source_path)),
        observations=[dict(observation("good"), review_text="bad"), observation("invalid", rid="r2", evidence="invented")],
        issues=[dict(issue_id="mixed", category="controls_input", title="mixed", review_ids=["r1", "r2"],
                     observation_ids=["good", "invalid", "good"])],
        category_issue_counts=dict(controls_input=1), category_observation_counts=dict(controls_input=2),
        summary=dict(reviews=2, observations=2, final_issues=1, none_reviews=0, failed_reviews=1),
        created_at="2026-09-21T00:00:00Z")), encoding="utf-8", newline="\n")
    partial = app._chatbi_snapshot(str(run_path), run_path.stat().st_mtime_ns, "test")
    detail = app.get_issue_detail("mixed", partial)
    assert detail["reviews"] == detail["observations"] == 1
    assert detail["review_ids"] == ["r1"] and detail["claim_counts"] == dict(complaint=1)
    assert len(detail["evidence_items"]) == 1
    assert detail["evidence_items"][0]["updated_at"] == "2026-09-03T00:00:00Z"
    assert detail["evidence_items"][0]["language"] == "english" and detail["evidence_items"][0]["recommended"] is True
    assert partial["global"]["issues"][0]["observations"] == 1
    assert partial["global"]["issues"][0]["claim_counts"] == dict(complaint=1)

# Evidence sorting and distinct-review limit apply after filters, including missing timestamps.
evidence_snapshot = dict(issues=[dict(id="a", title="A", category="controls_input", evidence_items=[
    dict(review_id="r1", observation_id="late", quote="bad", updated_at="2026-09-02T00:00:00Z"),
    dict(review_id="r2", observation_id="early", quote="bad", updated_at="2026-09-01T00:00:00Z"),
    dict(review_id="r1", observation_id="also-late", quote="another", updated_at="2026-09-02T00:00:00Z"),
    dict(review_id="r3", observation_id="missing", quote="bad", updated_at=None),
]), dict(id="b", title="B", category="visual_graphics", evidence_items=[
    dict(review_id="r4", observation_id="newest", quote="bad", updated_at="2026-09-03T00:00:00Z")])])
evidence = app.get_evidence(limit=2, snapshot=evidence_snapshot)
assert evidence["total"] == 4 and [row["review_id"] for row in evidence["items"]] == ["r4", "r1"]
filtered = app.get_evidence(category="controls_input", limit=10000, snapshot=evidence_snapshot)
assert [row["observation_id"] for row in filtered["items"]] == ["also-late", "early", "missing"]
assert app.get_evidence(category="visual_graphics", issue_id="a", snapshot=evidence_snapshot)["total"] == 0
for args in (dict(category="unknown"), dict(limit=0), dict(limit=10001), dict(limit=True)):
    try:
        app.get_evidence(snapshot=evidence_snapshot, **args)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid input accepted: {args}")

# Frozen real data: full global coverage and legacy metrics remain independently reproducible.
live = app.chatbi_snapshot()
global_view = app.get_overview(live)["global"]
assert live["metrics"] == dict(reviews=830, observations=377, issues=56, none_reviews=583,
                               issue_reviews=247, coverage=.2976)
assert (global_view["scope"]["analyzed"], global_view["scope"]["with_feedback"],
        global_view["scope"]["without_feedback"]) == (830, 247, 583)
assert (global_view["overlap"]["single"], global_view["overlap"]["multiple"]) == (153, 94)
assert global_view["top_categories"]["reviews"] == 233
assert len(global_view["categories"]) == 13 and len(global_view["issues"]) == 56
assert global_view["overlap"]["pairs"][0]["reviews"] == 48
assert global_view["timeline"]["dated"] + global_view["timeline"]["undated"] == global_view["scope"]["collected"]
assert sum(row["reviews"] for row in global_view["timeline"]["months"]) == global_view["timeline"]["dated"]
assert sum(row["reviews"] for row in global_view["timeline"]["years"]) == global_view["timeline"]["dated"]
assert app.get_evidence(limit=10000, snapshot=live)["total"] == 247
assert all(issue["evidence_items"] == sorted(issue["evidence_items"], key=rules.evidence_order) for issue in live["issues"])
assert rules.build(mixed_raw, mixed_source, [dict(id="mixed", category="controls_input")],
                   rules.analysis_inputs(mixed_raw, mixed_source, {}), app.CATEGORY_NAMES) == mixed
print("PASS: global counts, UTC publication timeline, statuses, direct issues, deduped claims, inclusive pairs, evidence and frozen 830-review snapshot")
