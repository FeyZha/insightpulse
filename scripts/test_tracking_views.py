"""One offline replay of the complete comparison flow, plus its trust boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.test_tracking import state_for, envelope, cache, observation, review
import app
import tracking
import tracking_views as views

DAY = 86400
EVENT = 1704672000


def replay():
    snapshots = []
    for sid, at, total in (("a", EVENT, 100), ("b", EVENT+3*DAY, 150), ("c", EVENT+7*DAY, 200)):
        rows = []
        for i in range(1, total+1):
            improved = sid == "c" and i <= 20
            complained = i <= 30 or (100 < i <= (130 if sid == "b" else 110))
            text = "crash is fixed" if improved else "crashes" if complained else "Enjoyable game"
            created = EVENT-DAY+i if i <= 100 else EVENT+DAY+i
            rows.append(dict(review_id=str(i), text=text, created_at=created,
                             updated_at=EVENT+2*DAY if improved else created,
                             language="english", recommended=not (complained and not improved)))
        snapshots.append(envelope(sid, at, rows))
    state = state_for(*snapshots)
    for snap in state["snapshots"]:
        snap["source"] = "replay"
        for row in snap["reviews"].values():
            if row["text"] == "crash is fixed":
                item = dict(observation("crash is fixed"), claim="improved", change_evidence="crash is fixed")
                cache(state, row, item)
            elif row["text"] == "crashes":
                cache(state, row, observation())
            else:
                cache(state, row)
    for snap in state["snapshots"]:
        tracking.freeze_basis(state, snap)
    return state


def scope(**changes):
    return dict(before="a", after="c", mode="snapshots", metric="complaints", language="all", offset="480", **changes)


class ComparisonReplay(unittest.TestCase):
    def test_source_membership_uses_identity_then_publication_time_without_analysis(self):
        old = [review("shared"), review("translated"), review("missing")]
        new = [dict(review("shared", "new text"), updated_at=15, recommended=False), review("translated"),
               dict(review("old"), created_at=10, updated_at=10),
               dict(review("new"), created_at=11, updated_at=11),
               dict(review("at_end"), created_at=20, updated_at=20), review("unknown"), review("future")]
        state = state_for(envelope("a", 10, old), envelope("c", 20, new + [new[2]]))
        a, b = state["snapshots"]
        b["reviews"]["translated"]["text_zh"] = "只有译文不同"
        b["reviews"]["unknown"]["created_at"] = 0
        b["reviews"]["future"]["created_at"] = 21  # Defensive check for invalid legacy cache dates.
        with patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("unexpected model")):
            result = views.comparison(state, scope())
        sources = result["source_breakdown"]
        self.assertEqual((sources["before_total"], sources["after_total"]), (3, 7))
        expected = dict(shared=["shared", "translated"], first_seen=["old"], new_review=["at_end", "new"],
                        unknown_time=["future", "unknown"], not_seen=["missing"], scope_entered=[], scope_left=[],
                        text_changed=["shared"], recommendation_changed=["shared"])
        self.assertEqual(sources["groups"], {k: dict(count=len(ids), review_ids=ids) for k, ids in expected.items()})
        self.assertEqual(result["after"]["pending"], 7)
        for timestamp in (None, False, float("nan"), float("inf"), -1):
            b["reviews"]["unknown"]["created_at"] = timestamp
            self.assertEqual(tracking.review_source(a, b, "unknown"), "unknown_time")

    def test_language_movement_is_not_a_new_review_or_disappearance(self):
        state = state_for(envelope("a", 10, [review("entered"), dict(review("left"), language="schinese"),
                                            dict(review("shared"), language="schinese")]),
                          envelope("c", 20, [dict(review("entered"), language="schinese"), review("left"),
                                             dict(review("shared"), language="tchinese")]))
        all_sources = views.comparison(state, scope())["source_breakdown"]
        sources = views.comparison(state, {**scope(), "language": "chinese"})["source_breakdown"]
        self.assertEqual(all_sources["groups"]["shared"]["count"], 3)
        self.assertEqual((sources["before_total"], sources["after_total"]), (2, 2))
        self.assertEqual({k: v["review_ids"] for k, v in sources["groups"].items() if v["count"]},
                         dict(shared=["shared"], scope_entered=["entered"], scope_left=["left"]))
        self.assertEqual(sources["language"], "chinese")

    def test_partial_samples_reject_deltas_and_evidence(self):
        state = replay()
        before = state["snapshots"][0]
        before.update(coverage="bounded_archive", scope=None)
        for rid in range(95,101): before["reviews"][str(rid)]["text"]="not yet analyzed"
        result=views.comparison(state,scope())
        self.assertFalse(result["comparable"])
        self.assertEqual(result["issues"],[])
        self.assertEqual((result["before"]["analyzed"],result["before"]["pending"]),(94,6))
        with self.assertRaises(ValueError): views.evidence(state,scope(),"issue","before")

    def test_more_complaints_with_the_same_share_are_not_a_rate_increase(self):
        state = state_for(*(envelope(sid, at, [review(str(i), "crashes" if i % 10 == 0 else "fine")
            for i in range(total)]) for sid, at, total in (("a", 10, 100), ("c", 20, 200))))
        for snap in state["snapshots"]:
            for row in snap["reviews"].values():
                cache(state, row, *([observation()] if row["text"] == "crashes" else []))
        for snap in state["snapshots"]: tracking.freeze_basis(state,snap)
        issue = views.comparison(state, scope())["issues"][0]
        self.assertEqual((issue["before_count"], issue["after_count"], issue["delta_pp"]), (10,20,0))

    def test_numbers_evidence_revision_and_timeline_agree_without_models(self):
        state = replay()
        with patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("unexpected model")):
            result = views.comparison(state, scope())
            self.assertTrue(result["comparable"], result["reasons"])
            issue = result["issues"][0]
            self.assertEqual((issue["before_count"], issue["after_count"]), (30,20))
            self.assertAlmostEqual(issue["delta_pp"],-20)
            self.assertEqual((result["before"]["complaints"], result["after"]["complaints"]), (30, 20))
            self.assertEqual(issue["after_improved"], 20)
            self.assertEqual(issue["after_mentions"], 40)
            for metric, expected in (("mentions", 40), ("complaints", 20), ("improved", 20)):
                selected = {**scope(), "metric": metric}
                evidence = views.evidence(state, selected, "issue", "after", 0, 50)
                self.assertEqual(evidence["total"], expected)
                self.assertEqual(len({r["review_id"] for r in evidence["items"]}), expected)
            trend = views.timeline(state, scope(), "issue")
            self.assertEqual([p["value"] for p in trend["points"]], [30, 40, 10])
            gaps = views.timeline(state, scope(), "issue", expected_hours=24, expected_since=tracking.pipeline.utc(EVENT))
            self.assertEqual([p["value"] for p in gaps["points"]], [30, None, 40, None, 10])
            future_schedule = views.timeline(state, scope(), "issue", expected_hours=24,
                                            expected_since=tracking.pipeline.utc(EVENT+8*DAY))
            self.assertEqual(len(future_schedule["points"]), 3)

    def test_periods_use_creation_time_and_frozen_side_versions(self):
        state = replay()
        event = dict(id="release", name="回放版本", at=tracking.pipeline.utc(EVENT), precision="time", url="")
        selected = {**scope(), "mode": "periods", "event_id": "release", **views.event_window(event, 7)}
        result = views.comparison(state, selected, [event])
        self.assertTrue(result["comparable"], result["reasons"])
        self.assertEqual((result["before"]["total"], result["after"]["total"]), (100, 100))
        self.assertEqual((result["issues"][0]["before_count"], result["issues"][0]["after_count"]), (30, 10))
        self.assertEqual(result["after"]["complaints"], 10)
        sources = result["source_breakdown"]
        self.assertEqual(sources["basis"], "saved_snapshots")
        self.assertEqual((sources["before_total"], sources["after_total"]), (100, 200))
        self.assertEqual(sources["groups"]["new_review"]["count"], 100)
        after = views.evidence(state, selected, "issue", "after", limit=50)
        self.assertTrue(all(int(r["review_id"]) > 100 for r in after["items"]))
        points = views.timeline(state, selected, "issue", [event], periods=True)["points"]
        self.assertEqual(sum(p["count"] or 0 for p in points), 40)
        self.assertEqual(len([p for p in points if p["total"]]), 2)
        wrong = {**selected, "before": "b"}
        self.assertFalse(views.comparison(state, wrong, [event])["comparable"])
        date_only = views.event_window({**event, "precision": "day"}, 7)
        self.assertEqual(tracking.flow.timestamp(date_only["after_start"]), EVENT+DAY)
        self.assertEqual(tracking.flow.timestamp(date_only["before_end"]), EVENT)

    def test_zero_small_missing_and_mixed_claims_keep_their_meaning(self):
        state = replay()
        version = state["snapshots"][-1]["reviews"]["1"]
        version["text"] = "crash is fixed but still crashes sometimes"
        cache(state, version, dict(observation("crash is fixed"), claim="improved", change_evidence="crash is fixed"),
              observation("still crashes sometimes"))
        state["snapshots"][-1].pop("tracking_basis")
        tracking.freeze_basis(state,state["snapshots"][-1]) # fixture assembled before its save
        result = views.comparison(state, scope())
        self.assertEqual(result["issues"][0]["after_improved"], 20)
        self.assertEqual(result["issues"][0]["after_complaints"], 21)
        self.assertEqual(result["after"]["complaints"], 21)
        state["snapshots"][1]["coverage"] = "bounded_recent_sample"
        self.assertIsNone(views.timeline(state, scope(), "issue")["points"][1]["value"])
        state["snapshots"][0]["reviews"] = {}
        result = views.comparison(state, scope())
        self.assertFalse(result["comparable"])
        self.assertEqual(result["issues"],[])

    def test_saved_scope_is_pinned_and_translation_does_not_change_identity(self):
        state = replay()
        with tempfile.TemporaryDirectory() as directory:
            prefs = views.save(directory, state, dict(kind="comparison", name="回放比较", scope=scope()))
            sid = prefs["comparisons"][0]["id"]
            self.assertEqual(views.saved_scope(directory, state, sid)["before"], "a")
            state["snapshots"][0]["reviews"]["1"]["text_zh"] = "译文更新"
            views.saved_scope(directory, state, sid)
            state["snapshots"][0]["tracking_basis"]["assignments"].clear()
            with self.assertRaisesRegex(ValueError, "分析结果已更新"):
                views.saved_scope(directory, state, sid)

    def test_input_validation_and_scheduler_do_not_start_unrequested_work(self):
        state = replay()
        for invalid in ({**scope(), "metric": "unknown"}, {**scope(), "after": "a"},
                        {**scope(), "offset": "9999"}, {**scope(), "before": ["a"]}):
            with self.assertRaises((ValueError, TypeError)):
                views.comparison(state, invalid)
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "ROOT", Path(directory)), \
                patch.object(app, "refresh_tracking") as run:
            self.assertFalse(app.tracking_schedule_tick())
            run.assert_not_called()
            for body in (dict(kind="schedule", enabled="false"),
                         dict(kind="event", name="x", at=EVENT, url="javascript:alert(1)"),
                         dict(kind="event", name="x", at=1e100)):
                with self.assertRaises(ValueError):
                    views.save(directory, state, body)
            prefs = views.save(directory, state, dict(kind="schedule", enabled=True, analyze=False, interval_hours=24))
            due = tracking.flow.timestamp(prefs["schedule"]["next_run_at"])
            def finish(*args):
                app.TRACKING_JOB.update(status="complete", message="offline capture")
                app.JOB_LOCK.release()
            run.side_effect = finish
            self.assertFalse(app.tracking_schedule_tick(due-1))
            self.assertTrue(app.tracking_schedule_tick(due))
            run.assert_called_once_with(True, False)
            self.assertFalse(app.tracking_schedule_tick(due))


if __name__ == "__main__":
    unittest.main()
