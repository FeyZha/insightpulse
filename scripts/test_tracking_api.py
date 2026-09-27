"""Offline HTTP checks against real cached tracking data; no collection or LLM calls."""
from datetime import datetime
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class TrackingApiChecks(unittest.TestCase):
    def test_monitoring_reads_all_points_without_analysis_calls(self):
        from scripts.test_issue_tracking import scenario
        state = dict(scenario(1, 2, 10, 10), game="dinner-date")
        with patch.object(app, "product_state", return_value=state), \
                patch.object(app.tracking_views, "tracking_state", return_value=state):
            data = self.data("/api/tracking/monitoring?game=dinner-date")
            self.assertEqual(len(data["points"]), 2)
            evidence = self.data("/api/tracking/monitoring/evidence?game=dinner-date&snapshot_id=a&issue_id=issue")
            self.assertEqual(evidence["total"], 1)
            self.assert_error(self.request("/api/tracking/monitoring?game=unknown"), 400)
            self.assert_error(self.request("/api/tracking/monitoring/evidence?snapshot_id=other&issue_id=issue"), 400)
        self.model.assert_not_called()
        self.collect.assert_not_called()

    def test_decision_followup_reuses_comparison_and_checks_full_definition(self):
        from copy import deepcopy
        from scripts.test_issue_tracking import scenario
        state = dict(scenario(1, 2, 10, 10), game="dinner-date")
        with patch.object(app, "product_state", return_value=state), \
                patch.object(app.tracking_views, "tracking_state", return_value=state):
            result = self.data("/api/chatbi/decisions/comparison?game=dinner-date")
            comparison = result["comparison"]
            row = comparison["issues"][0]
            self.assertEqual((row["before_count"], row["after_count"], row["delta_pp"]), (1, 2, 10))
            self.assertEqual((comparison["before"]["analyzed"], comparison["after"]["analyzed"]), (10, 10))
            altered = deepcopy(state)
            altered["issues"]["issue"]["canonical_description"] = "different definition with same ID and title"
            with patch.object(app, "product_state", return_value=altered):
                mismatch = self.data("/api/chatbi/decisions/comparison?game=dinner-date")
            self.assertEqual(mismatch["comparison"]["issues"], [])
            self.assertIn("issue", mismatch["issue_reasons"])
            state["snapshots"] = state["snapshots"][:1]
            waiting = self.data("/api/chatbi/decisions/comparison?game=ostranauts")
            self.assertIsNone(waiting["comparison"])
            self.assertIn("等待第二次采集", waiting["reasons"][0])
        self.model.assert_not_called()
        self.collect.assert_not_called()

    def test_analyze_action_completes_selected_pair_and_reuses_saved_result(self):
        from copy import deepcopy
        from scripts.test_issue_tracking import scenario
        state = dict(scenario(1, 2, 3, 7), game="dinner-date")
        ready = app.tracking.prepare_reanalysis(state, state, {"label": "fixture", "files": [{"path": "fixture"}]})
        partial = deepcopy(ready)
        partial["cache"].pop(app.tracking._cache_key(partial["snapshots"][0]["reviews"]["1000"]))
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "ROOT", Path(directory)), \
                patch.object(app, "product_state", return_value=state), \
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-only"}):
            app.tracking.save_reanalysis(directory, partial)
            def complete(value, ids):
                self.assertEqual(ids, {"a", "b"})
                value.update(deepcopy(ready))
                return value
            with patch.object(app.tracking, "complete_reanalysis", side_effect=complete) as analysis:
                body = dict(game="dinner-date", before="a", after="b")
                status, result = self.request("/api/tracking/analyze", body)
                self.assertEqual(status, 200, result)
                self.assertTrue(result["data"]["comparable"])
                self.assertEqual(result["data"]["issues"][0]["after_count"], 2)
                self.assertEqual(self.request("/api/tracking/analyze", body)[0], 200)
                analysis.assert_called_once()
                self.assert_error(self.request("/api/tracking/analyze", dict(body, before="b")), 400)
                self.assert_error(self.request("/api/tracking/analyze", body, token=False), 403)
            self.assertFalse(app.JOB_LOCK.locked())
            evidence = self.data("/api/tracking/evidence?before=a&after=b&issue_id=issue&side=before")
            self.assertEqual(evidence["items"][0]["snapshot_id"], "a")

    def test_configured_static_directory_is_served_without_overwriting_default(self):
        default = app.ROOT / "frontend/dist/index.html"
        original = default.read_bytes()
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"INSIGHTPULSE_FRONTEND_DIR": directory}):
            Path(directory, "index.html").write_text('<script src="/assets/new.js"></script>', encoding="utf-8")
            Path(directory, "assets").mkdir()
            Path(directory, "assets/new.js").write_text("new build", encoding="utf-8")
            with urlopen(self.base + "/") as response:
                self.assertIn(b"/assets/new.js", response.read())
            with urlopen(self.base + "/assets/new.js") as response:
                self.assertEqual(response.read(), b"new build")
        self.assertEqual(default.read_bytes(), original)

    @classmethod
    def setUpClass(cls):
        cls.old_port = app.PORT
        cls.old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        cls.model_patch = patch.object(app.p, "deepseek", side_effect=AssertionError("Offline test called a model"))
        cls.collect_patch = patch.object(app, "collect", side_effect=AssertionError("Offline test collected remote data"))
        cls.model = cls.model_patch.start()
        cls.collect = cls.collect_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        app.PORT = cls.server.server_port
        cls.base = f"http://127.0.0.1:{app.PORT}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        app.PORT = cls.old_port
        cls.model_patch.stop()
        cls.collect_patch.stop()
        if cls.old_key is not None:
            os.environ["DEEPSEEK_API_KEY"] = cls.old_key

    def request(self, path, body=None, *, token=True, origin=None):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            if token is not None:
                headers["X-InsightPulse-Token"] = app.TOKEN if token is True else token
        if origin:
            headers["Origin"] = origin
        request = Request(self.base + path, method="GET" if body is None else "POST",
                          data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def data(self, path):
        status, envelope = self.request(path)
        self.assertEqual(status, 200, envelope)
        self.assertEqual(set(envelope), {"data"})
        return envelope["data"]

    def assert_error(self, response, status):
        actual, envelope = response
        self.assertIn(actual, (status,) if isinstance(status, int) else status, envelope)
        self.assertEqual(set(envelope), {"error"})
        self.assertTrue(envelope["error"]["code"])
        self.assertTrue(envelope["error"]["message"])
        return envelope["error"]

    def wait_for_job(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.data("/api/tracking")["job"]
            if job["status"] != "running":
                return job
            time.sleep(0.02)
        self.fail("Tracking job did not finish")

    def test_real_catalog_and_history_work_without_models(self):
        catalog=self.data("/api/tracking")
        self.assertIn(catalog["status"], ("ready", "unavailable"))
        query=urlencode(catalog["selection"])
        result=self.data("/api/tracking/compare?"+query)
        self.assertEqual(result["comparable"], catalog["status"] == "ready")
        if not result["comparable"]:
            self.assertTrue(result["reasons"])
            self.assertEqual(result["issues"], [])
        self.assertNotIn("trend_counts",result)
        state=app.product_state("dinner-date")
        rid=next(iter(state["snapshots"][-1]["reviews"]))
        history=self.data("/api/tracking/reviews/"+rid)
        self.assertEqual(history["review_id"],rid)
        self.assertEqual(len(history["history"]),len(catalog["snapshots"]))
        self.model.assert_not_called()
        self.collect.assert_not_called()

    def test_invalid_time_order_and_unknown_records_have_structured_errors(self):
        snapshots = self.data("/api/tracking")["snapshots"]
        for before, after in ((snapshots[-1]["id"], snapshots[0]["id"]),
                              (snapshots[0]["id"], snapshots[0]["id"]),
                              ("not-a-snapshot", snapshots[-1]["id"])):
            with self.subTest(before=before, after=after):
                self.assert_error(self.request("/api/tracking/compare?" + urlencode({"before": before, "after": after})), 400)
        self.assert_error(self.request("/api/tracking/compare"), 400)
        self.assert_error(self.request("/api/tracking/reviews/999999999999999999999999"), (400, 404))
        self.assert_error(self.request("/api/tracking/reviews/not-a-review"), 400)
        self.assert_error(self.request("/api/tracking/timeline?issue_id=not-an-issue"), (400, 404))

    def test_refresh_requires_token_valid_origin_and_strict_booleans(self):
        body = {"collect": False, "analyze": False}
        for path in ("/api/tracking/refresh", "/api/tracking/save"):
            for token in (None, "invalid-token"):
                error = self.assert_error(self.request(path, body, token=token), 403)
                self.assertEqual(error["code"], "REQUEST_REJECTED")
            self.assert_error(self.request(path, body, origin="https://outside.example"), 403)
        for field in ("collect", "analyze"):
            for invalid in (0, 1, "false", None, []):
                with self.subTest(field=field, invalid=invalid):
                    self.assert_error(self.request("/api/tracking/refresh", dict(body, **{field: invalid})), 400)
        error = self.assert_error(self.request("/api/tracking/refresh", {"collect": False, "analyze": True}), 409)
        self.assertEqual(error["code"], "API_KEY_MISSING")
        self.model.assert_not_called()
        self.collect.assert_not_called()

    def test_game_isolation_and_no_production_demo(self):
        self.assert_error(self.request("/api/tracking?demo=2"),400)
        dinner=self.data("/api/tracking")
        other=self.data("/api/tracking?game=ostranauts")
        other_ids={s["id"] for s in other["snapshots"]}
        self.assertTrue(other_ids)
        self.assertTrue(other_ids.isdisjoint(s["id"] for s in dinner["snapshots"]))
        if other["selection"]:
            self.assertTrue(set(other["selection"].values()) <= other_ids)
        query=urlencode(dinner["selection"])
        self.assert_error(self.request("/api/tracking/compare?game=ostranauts&"+query),400)
        self.assert_error(self.request("/api/tracking/compare?"+query+"&metric=complaints"),400)
        self.assert_error(self.request("/api/tracking/compare?"+query+"&mode=periods"),400)
        self.assert_error(self.request("/api/tracking/save",{"kind":"comparison","scope":{"demo":"2"}}),400)
        self.assertEqual(self.data("/api/tracking")["selection"],dinner["selection"])

    def test_fixture_evidence_over_http(self):
        from scripts.test_issue_tracking import scenario
        state=dict(scenario(1,2,3,7),game="test-fixture")
        with patch.object(app,"product_state",return_value=state):
            result=self.data("/api/tracking/compare?before=a&after=b")
            self.assertEqual(result["status"],"ready")
            for side,count in (("before",1),("after",2)):
                evidence=self.data("/api/tracking/evidence?before=a&after=b&issue_id=issue&side="+side)
                self.assertEqual(evidence["total"],count)
                self.assertEqual(evidence["snapshot_id"],"a" if side=="before" else "b")

    def test_async_cached_refresh_rejects_concurrent_refresh_and_finishes(self):
        state = app.tracking.history(app.ROOT)
        entered, release = threading.Event(), threading.Event()
        def refresh(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Offline refresh barrier timed out")
            self.assertFalse(kwargs.get("live", False))
            return state
        with patch.object(app.tracking, "refresh", side_effect=refresh) as fake:
            try:
                status, envelope = self.request("/api/tracking/refresh", {"collect": False, "analyze": False})
                self.assertEqual(status, 202, envelope)
                self.assertEqual(envelope["data"]["status"], "running")
                self.assertTrue(entered.wait(2))
                self.assert_error(self.request("/api/tracking/refresh", {"collect": False, "analyze": False}), 409)
            finally:
                release.set()
                job = self.wait_for_job()
            self.assertEqual(job["status"], "complete", job)
            fake.assert_called_once()
        self.assertFalse(app.JOB_LOCK.locked())
        self.model.assert_not_called()
        self.collect.assert_not_called()

    def test_async_failure_is_visible_and_releases_the_busy_lock(self):
        with patch.object(app.tracking, "refresh", side_effect=ValueError("offline refresh failure")):
            status, envelope = self.request("/api/tracking/refresh", {"collect": False, "analyze": False})
            self.assertEqual(status, 202, envelope)
            job = self.wait_for_job()
            self.assertEqual(job["status"], "error")
            self.assertIn("offline refresh failure", job["message"])
        self.assertFalse(app.JOB_LOCK.locked())
        state = app.tracking.history(app.ROOT)
        with patch.object(app.tracking, "refresh", return_value=state):
            status, envelope = self.request("/api/tracking/refresh", {"collect": False, "analyze": False})
            self.assertEqual(status, 202, envelope)
            self.assertEqual(self.wait_for_job()["status"], "complete")


if __name__ == "__main__":
    unittest.main()
