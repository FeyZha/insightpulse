"""Offline contracts, context inheritance, frozen results and existing domain consistency."""
from copy import deepcopy
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app
import analysis_ui as ui
import tracking
import tracking_views as views
from scripts.test_tracking_views import replay, scope
from scripts.test_tracking import cache, observation


def simulated(queries=None, filters=None, followup=False, comparison=False, blocks=None):
    """A labelled model substitute, not a claim of real model validation."""
    def model(prompt, payload, validator, audit):
        if prompt == ui.PLAN_PROMPT:
            value = {"followup": followup, "needs_comparison": comparison, "filters": filters or {},
                     "queries": queries if queries is not None else [{"name": "issues"}]}
        else:
            ref = next(iter(payload["results"]))
            value = {"summary": [{"data_ref": ref, "fact_id": "f0"}], "blocks": blocks if blocks is not None else
                     [{"type": r["kind"], "data_ref": k} for k, r in list(payload["results"].items())[:3]]}
        audit({"status": "passed", "model": "SIMULATED"})
        return validator(json.dumps(value))
    return model


def fixture():
    return ui.build("核查前后投诉和改善", scope(), replay(), simulated([
        {"name": "compare"}, {"name": "trend", "issue_ids": ["issue"]}, {"name": "overview"}], comparison=True))


class AnalysisContract(unittest.TestCase):
    def test_comparison_reuses_stats_and_evidence(self):
        response = fixture()
        self.assertEqual(response["mode"], "model")  # The simulated path runs BOTH validators.
        self.assertEqual(len(response["model_calls"]), 2)
        data = response["results"]["r0"]
        self.assertEqual(data["source_breakdown"], views.comparison(replay(), scope())["source_breakdown"])
        self.assertTrue(any(data["source_breakdown"]["summary"] in f for f in data["facts"].values()))
        row = data["items"][0]
        self.assertEqual((row["before_count"], row["after_count"], row["before_share"], row["after_share"]), (30, 20, 30, 10))
        self.assertAlmostEqual(row["delta_pp"], -20)
        self.assertIn("30% → 10%", response["summary"]["text"])
        self.assertIn("下降20个百分点", response["summary"]["text"])
        for side in ("before", "after"):
            rows = row["evidence"][side]["items"]
            self.assertEqual(len({r["review_id"] for r in rows if any(o["claim"] in ("complaint", "worsened") for o in r["observations"])}), row[side+"_count"])
            self.assertTrue(all(r["review_version_id"] and r["created_at"] and r["observed_at"] for r in rows))

    def test_real_current_samples_match_fixed_pages(self):
        state = app.tracking_history()
        filters = dict(before=state["snapshots"][0]["id"], after=state["snapshots"][-1]["id"], mode="snapshots", metric="complaints")
        r = ui.build("样本变化", filters, state, simulated([{"name": "compare", "limit": 10}]))
        data = r["results"]["r0"]
        expected_denominators = tuple(sum(tracking._cache_key(v) in state["cache"] for v in snap["reviews"].values())
                                      for snap in (state["snapshots"][0], state["snapshots"][-1]))
        self.assertEqual((data["before"]["analyzed"], data["after"]["analyzed"]), expected_denominators)
        self.assertFalse(data["comparable"])
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(data["items"], [])
        expected = views.comparison(state, filters)
        self.assertEqual(data["source_breakdown"], expected["source_breakdown"])
        groups = data["source_breakdown"]["groups"]
        self.assertEqual({k: groups[k]["count"] for k in ("shared", "first_seen", "new_review", "text_changed")},
                         dict(shared=100, first_seen=726, new_review=4, text_changed=9))
        self.assertEqual([i["delta_pp"] for i in data["items"]], [i["delta_pp"] for i in expected["issues"][:10]])
        current = ui.execute(state, ui.context({}, state), {"name": "issues", "limit": 10})
        fixed = {r["id"]: r for r in app.get_overview()["global"]["issues"]}
        self.assertEqual(current["denominator"], app.get_overview()["global"]["scope"]["analyzed"])
        for row in current["items"]:
            self.assertEqual(row["mentions"], fixed[row["id"]]["reviews"])

    def test_filter_followup_retains_task_scope_and_updates_every_result(self):
        state = replay()
        for snap in state["snapshots"]:
            for rid, row in snap["reviews"].items():
                row["language"] = "schinese" if int(rid) % 2 == 0 else "english"
        first = ui.build("前后变化", scope(), state, simulated([{"name": "compare"}], comparison=True))
        second = ui.build("只看中文评论", first["context"]["filters"], state,
                          simulated(filters={"language": "chinese"}, followup=True), first)
        self.assertEqual(second["analysis_question"], first["analysis_question"])
        self.assertEqual(second["context"]["filters"]["before"], "a")
        data = second["results"]["r0"]
        self.assertEqual((data["before"]["analyzed"], data["after"]["analyzed"]), (50, 100))
        sources = data["source_breakdown"]
        self.assertEqual((sources["before_total"], sources["after_total"]), (50, 100))
        self.assertTrue(all(int(rid) % 2 == 0 for g in sources["groups"].values() for rid in g["review_ids"]))
        self.assertIn("15/50 → 10/100", second["summary"]["text"])
        self.assertTrue(all(r["language"] == "schinese" for r in data["items"][0]["evidence"]["after"]["items"]))
        self.assertEqual(first["results"]["r0"]["before"]["analyzed"], 100)
        self.assertNotEqual(first["context"]["data_version"], second["context"]["data_version"])

    def test_explicit_time_filter_updates_summary_chart_and_evidence(self):
        state = replay()
        first = ui.build("投诉概览", {}, state, simulated())
        second = ui.build("只看2024-01-08到2024-01-10", first["context"]["filters"], state,
                          simulated(filters={"start": "2024-01-08", "end": "2024-01-10"}, followup=True), first)
        data = second["results"]["r0"]
        snap = ui.selected(state, second["context"]["filters"])[0]
        self.assertEqual(data["denominator"], len(snap["reviews"]))
        self.assertIn(f"/{data['denominator']}", second["summary"]["text"])
        self.assertNotEqual(data["denominator"], first["results"]["r0"]["denominator"])
        self.assertTrue(all(r["review_id"] in snap["reviews"] for row in data["items"] for r in row["evidence"]["after"]["items"]))

    def test_missing_comparison_never_invents_windows(self):
        r = ui.build("更新前后哪些变化最大", {}, replay(), simulated([{"name": "compare"}], comparison=True))
        self.assertEqual(r["blocks"][0]["type"], "range")
        self.assertNotIn("before", r["context"]["filters"])

    def test_mixed_claims_keep_improvement_and_counterexamples(self):
        state = replay()
        row = state["snapshots"][-1]["reviews"]["1"]
        row["text"] = "crash is fixed but still crashes"
        cache(state, row, dict(observation("crash is fixed"), claim="improved", change_evidence="crash is fixed"), observation("still crashes"))
        state["snapshots"][-1].pop("tracking_basis")
        tracking.freeze_basis(state,state["snapshots"][-1]) # fixture assembled before save
        r = ui.build("真的改善了吗", scope(), state, simulated([{"name": "compare"}]))
        item = r["results"]["r0"]["items"][0]
        self.assertEqual((item["after_complaints"], item["after_improved"]), (21, 20))
        evidence = next(r for r in item["evidence"]["after"]["items"] if r["review_id"] == "1")
        self.assertEqual({o["claim"] for o in evidence["observations"]}, {"complaint", "improved"})

    def test_unknown_queries_component_refs_parameters_and_text_are_rejected(self):
        r = fixture()
        valid = {"summary": r["summary"]["refs"], "blocks": r["blocks"]}
        for bad in ({**valid, "summary": [{"data_ref": "forged", "fact_id": "f0"}]},
                    {**valid, "answer": "版本已修复"}, {**valid, "blocks": [{"type": "html", "data_ref": "r0"}]},
                    {**valid, "blocks": [{"type": "comparison", "data_ref": "r0", "limit": 999}]},
                    {**valid, "blocks": [{"type": "comparison", "data_ref": "r0", "html": "<script/>"}]}):
            with self.assertRaises(ValueError): ui.validate_display(json.dumps(bad), r["results"])
        for query in ({"name": "sql"}, {"name": "issues", "issue_ids": ["forged"]}, {"name": "issues", "limit": True}):
            with self.assertRaises(ValueError):
                ui.validate_plan(json.dumps({"followup": False, "needs_comparison": False, "filters": {}, "queries": [query]}), replay(), ui.context({}, replay()), "核查")

    def test_model_unavailable_query_failure_and_no_data(self):
        state = replay()
        r = ui.build("核查投诉", {}, state, lambda *a: self.fail("model called without credentials"), configured=False)
        self.assertEqual(r["mode"], "fallback")
        self.assertTrue(r["warnings"])
        with patch.object(ui.views, "timeline", side_effect=OSError("offline")):
            r = ui.build("核查投诉", scope(), state, simulated([{"name": "trend"}, {"name": "compare"}]))
        self.assertTrue(any("查询失败" in w for w in r["warnings"]))
        self.assertEqual(r["blocks"][0]["type"], "comparison")
        state["snapshots"][-1]["reviews"] = {}
        r = ui.build("核查投诉", {}, state, simulated())
        self.assertEqual(r["results"]["r0"]["denominator"], 0)
        self.assertIn("未观察到", r["summary"]["text"])

    def test_model_controls_order_and_plain_text_is_supported(self):
        r = ui.build("只用文字说明", {}, replay(), simulated(blocks=[]))
        self.assertEqual(r["blocks"], [])
        r = ui.build("先看原话再看概况", {}, replay(), simulated([{"name": "overview"}, {"name": "evidence"}],
                     blocks=[{"type": "evidence", "data_ref": "r1"}, {"type": "stats", "data_ref": "r0"}]))
        self.assertEqual([b["type"] for b in r["blocks"]], ["evidence", "stats"])

    def test_new_subject_keeps_time_range_but_replaces_previous_queries(self):
        state = replay()
        previous = ui.build("全局变化", scope(), state, simulated([{"name": "overview"}]))
        response = ui.build("单独核查崩溃", previous["context"]["filters"], state,
            simulated([{"name": "compare", "issue_ids": ["issue"]}], followup=True), previous)
        self.assertEqual(response["context"]["filters"]["before"], "a")
        self.assertEqual(response["context"]["filters"]["issue_ids"], ["issue"])
        self.assertEqual(response["analysis_question"], "单独核查崩溃")
        self.assertEqual(response["blocks"][0]["type"], "comparison")

    def test_history_is_one_identity_frozen_at_the_current_cutoff(self):
        state = replay()
        data = ui.execute(state, ui.context({"snapshot": "b"}, state), {"name": "review_history", "review_id": "1"})
        self.assertEqual(data["review_id"], "1")
        self.assertEqual([row["snapshot"]["id"] for row in data["history"]], ["a", "b"])

    def test_invalid_model_output_degrades_without_rendering_fake_summary(self):
        def invalid(prompt, payload, validator, audit):
            return validator('{"answer":"版本已修复","html":"<script>bad()</script>"}')
        response = ui.build("核查反馈", scope(), replay(), invalid)
        self.assertEqual(response["mode"], "fallback")
        self.assertNotIn("<script>", json.dumps(response))
        self.assertIn("30/100 → 20/200", response["summary"]["text"])

    def test_http_persistence_and_context_replacement(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(app, "DB", Path(directory) / "test.sqlite3"))
            stack.enter_context(patch.object(app, "tracking_history", return_value=replay()))
            stack.enter_context(patch.object(app.p, "deepseek", side_effect=simulated([{"name": "compare"}], comparison=True)))
            stack.enter_context(patch.dict(os.environ, {"DEEPSEEK_API_KEY": "SIMULATED"}))
            app.init_db()
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            stack.enter_context(patch.object(app, "PORT", server.server_port))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            def request(path, body=None):
                req = Request(f"http://127.0.0.1:{server.server_port}"+path,
                    data=json.dumps(body).encode() if body else None,
                    headers={"Content-Type": "application/json", "X-InsightPulse-Token": app.TOKEN})
                with urlopen(req) as response: return json.load(response)["data"]
            try:
                r = request("/api/analysis/ask", {"question": "前后变化", "context": scope()})
                history = request("/api/analysis/turns/"+r["id"])
                self.assertEqual(history, [r])
                r2 = request("/api/analysis/ask", {"question": "更新前后", "context": {"mode": "current"}, "replace_context": True, "parent_id": r["id"]})
                self.assertEqual(r2["blocks"][0]["type"], "range")
                self.assertEqual(len(request("/api/analysis/turns/"+r2["id"])), 2)
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__":
    unittest.main()
