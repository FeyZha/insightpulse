"""Fixed-definition fixtures, exclusively in memory / temporary directories."""
import copy
import unittest
from unittest.mock import patch

from scripts.test_tracking import state_for, envelope, review, cache, observation
import tracking
import tracking_views as views


def scenario(old=10, new=40, total_a=100, total_b=100):
    state = state_for(*(envelope(sid, at, [review(str(i+base), "crashes" if i < count else "fine")
        for i in range(total)]) for sid, at, base, count, total in
        (("a", 10, 1000, old, total_a), ("b", 20, 2000, new, total_b))))
    for snap in state["snapshots"]:
        for row in snap["reviews"].values():
            cache(state, row, *([observation()] if row["text"] == "crashes" else []))
    for snap in state["snapshots"]:
        tracking.freeze_basis(state, snap)
    return state


class IssueTrackingChecks(unittest.TestCase):
    def test_counts_shares_and_no_business_states(self):
        for args in ((0, 20), (20, 0), (1, 2), (10, 20, 100, 200), (1, 1, 3, 7)):
            with self.subTest(args=args):
                result = tracking.compare(scenario(*args), "a", "b")
                self.assertTrue(result["comparable"], result["reasons"])
                row = result["issues"][0]
                da, db = args[2:] if len(args) == 4 else (100, 100)
                self.assertEqual((row["before_count"], row["after_count"]), args[:2])
                self.assertEqual(row["delta_pp"], 100*(args[1]/db-args[0]/da))
                self.assertEqual(row["delta_count"], args[1]-args[0])
                self.assertFalse({"status", "trend_status", "match", "confidence"}.intersection(row))
        self.assertEqual(tracking.compare(scenario(10,20,100,200),"a","b")["issues"][0]["delta_pp"],0)
        self.assertIn("不代表问题已解决",tracking.compare(scenario(2,0),"a","b")["issues"][0]["note"])

    def test_duplicates_no_issue_reviews_and_overlap(self):
        state = scenario(1, 2, 3, 4)
        a,b = state["snapshots"]
        b["reviews"] = copy.deepcopy(a["reviews"])
        for snap in (a,b):
            snap.pop("tracking_basis")
            row = snap["reviews"]["1000"]
            row["text"] = "crashes at launch; crashes loading"
            cache(state,row,observation("crashes at launch","launch"),observation("crashes loading","loading"))
        for snap in (a,b): tracking.freeze_basis(state,snap)
        result = tracking.compare(state,"a","b")
        row = result["issues"][0]
        self.assertEqual((result["before"]["analyzed"],result["after"]["analyzed"]),(3,3))
        self.assertEqual((row["before_count"],row["after_count"]),(1,1))
        self.assertEqual(row["before_observation_count"],2)
        self.assertEqual(row["delta_pp"],0)

    def test_missing_and_invalid_provenance_block_the_whole_comparison(self):
        def missing_cache(s): s["cache"].pop(next(iter(s["cache"])))
        def change_definition(s): s["snapshots"][1]["tracking_basis"]["definitions"]["issue"]["canonical_description"]="audio"
        def no_basis(s): s["snapshots"][1].pop("tracking_basis")
        def changed_membership(s): s["snapshots"][1]["tracking_basis"]["assignments"].clear()
        def changed_game(s): s["snapshots"][1]["app_id"] = 1022980
        for mutate in (missing_cache,change_definition,no_basis,changed_membership,changed_game,
                       lambda s:s["snapshots"][1].update(sample_kind="period")):
            s=scenario(); mutate(s); result=tracking.compare(s,"a","b")
            self.assertEqual(result["status"],"unavailable")
            self.assertTrue(result["reasons"])
            self.assertEqual(result["issues"],[])
        result=tracking.compare(scenario(0,1,0,3),"a","b")
        self.assertIn("分母为零"," ".join(result["reasons"]))

    def test_same_ids_titles_and_version_do_not_prove_same_definitions(self):
        s=scenario(); b=s["snapshots"][1]; b.pop("tracking_basis")
        b["issues"]=copy.deepcopy(s["issues"])
        b["issues"]["issue"]["canonical_description"]="different boundary"
        tracking.freeze_basis(s,b)
        self.assertFalse(tracking.compare(s,"a","b")["comparable"])

    def test_evidence_is_snapshot_local_and_cache_signature_changes(self):
        s=state_for(envelope("a",10,[review(text="crashes before")]),envelope("b",20,[review(text="crashes after")]))
        for snap in s["snapshots"]: cache(s,snap["reviews"]["1"],observation())
        for snap in s["snapshots"]: tracking.freeze_basis(s,snap)
        scope=dict(before="a",after="b")
        for side,word,sid in (("before","before","a"),("after","after","b")):
            data=views.evidence(s,scope,"issue",side)
            self.assertEqual(data["total"],1)
            self.assertEqual(data["items"][0]["text"],"crashes "+word)
            self.assertEqual(data["items"][0]["snapshot_id"],sid)
            self.assertTrue(data["items"][0]["review_version_id"])
        signature=views.signature(s,scope)
        s["game"]="other"
        self.assertNotEqual(signature,views.signature(s,scope))
        s["snapshots"][1]["reviews"]["1"]["text"]="changed"
        with self.assertRaises(ValueError): views.evidence(s,scope,"issue","after")

    def test_waiting_unavailable_and_latest_two_selection(self):
        s=scenario(); s["snapshots"]=s["snapshots"][:1]
        self.assertEqual(tracking.catalog(s)["status"],"waiting")
        s=scenario(); s["snapshots"][1].pop("tracking_basis")
        self.assertEqual(tracking.catalog(s)["status"],"unavailable")
        s=scenario(); s["snapshots"].append(dict(s["snapshots"][-1],id="c",observed_at=30,sample_kind="period"))
        result=tracking.catalog(s)
        self.assertEqual(result["selection"],dict(before="b",after="c"))
        self.assertEqual(result["status"],"unavailable")
        self.assertIn("最近两个保存时点",result["selection_note"])

    def test_freeze_does_not_overwrite_and_no_models_called(self):
        s=scenario(); old=copy.deepcopy(s["snapshots"][0]["tracking_basis"])
        with patch.object(tracking.pipeline,"deepseek",side_effect=AssertionError("model called")):
            tracking.freeze_basis(s,s["snapshots"][0]); tracking.compare(s,"a","b")
        self.assertEqual(old,s["snapshots"][0]["tracking_basis"])

    def test_multiple_issues_count_the_same_review_independently(self):
        s=scenario(1,1,3,3)
        for snap in s["snapshots"]:
            snap.pop("tracking_basis")
            row=next(iter(snap["reviews"].values()))
            row["text"]="crashes at launch; crashes loading"
            first,second=observation("crashes at launch","launch"),observation("crashes loading","loading")
            cache(s,row,first,second)
            s["assignments"][tracking._signature(row["review_id"],second)]="loading"
        s["issues"]["loading"]=dict(s["issues"]["issue"],issue_id="loading",canonical_description="Loading crash")
        for snap in s["snapshots"]: tracking.freeze_basis(s,snap)
        result=tracking.compare(s,"a","b")
        self.assertEqual(len(result["issues"]),2)
        self.assertTrue(all(row["before_count"]==row["after_count"]==1 for row in result["issues"]))
        self.assertEqual(result["before"]["analyzed"],3)

    def test_fixture_report_cannot_touch_real_data_and_snapshot_freeze_survives_reload(self):
        import json
        from pathlib import Path
        import tempfile
        from scripts.run_tracking_baseline import ROOT, report
        before={str(p):p.read_bytes() for p in (ROOT/"data").glob("steam-*.json")}
        result=report()
        self.assertEqual(set(result["real"]), {"dinner-date", "ostranauts"})
        self.assertTrue(all(rows["snapshots"] and not any(s["demo"] for s in rows["snapshots"])
                            for rows in result["real"].values()))
        self.assertEqual(before,{str(p):p.read_bytes() for p in (ROOT/"data").glob("steam-*.json")})
        raw=envelope("a",10,[review()])
        s=state_for(raw);cache(s,s["snapshots"][0]["reviews"]["1"],observation())
        tracking.freeze_basis(s,s["snapshots"][0])
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder)/"data";data.mkdir()
            (data/"steam-real.json").write_text(json.dumps(raw),encoding="utf-8")
            fake=envelope("fake",20,[review()]);fake["snapshot"]["demo"]=True
            (data/"steam-fixture.json").write_text(json.dumps(fake),encoding="utf-8")
            loaded=tracking._snapshots(folder,s)
            self.assertEqual([snap["id"] for snap in loaded],["a"])
            self.assertEqual(loaded[0]["tracking_basis"],s["snapshots"][0]["tracking_basis"])

if __name__ == "__main__": unittest.main()
