"""Offline integration checks for translation order, evidence and stable identities."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import data_flow_v2 as flow
import review_translation as translation
import tracking
from scripts.collect_sample import save_capture
from scripts.translate_reviews import translate_game
from scripts.test_tracking import cache, envelope, observation, review, state_for

TEXTS = ("The game crashes on launch.", "After update, the game still crashes on launch.")
QUOTE = "crashes on launch"


class TranslationIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.revision = 1
        self.translated = {}
        self.translation_calls = []
        self.events = []
        games = {name: dict(config, folder=self.root / config["folder"].relative_to(ROOT))
                 for name, config in app.GAMES.items()}
        for name, value in (("ROOT", self.root), ("RUNTIME", self.root / "runtime"), ("GAMES", games)):
            mock = patch.object(app, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        for cached in (app._current_product_state, app._chatbi_snapshot):
            cached.cache_clear()
            self.addCleanup(cached.cache_clear)

    def fixture(self, pending=False):
        raws = [envelope(sid, at, [dict(review(text=text), updated_at=updated)])
                for sid, at, updated, text in zip(("a", "b"), (10, 20), (2, 15), TEXTS)]
        for raw in raws:
            (self.data_dir / f"steam-{raw['snapshot']['snapshot_id']}.json").write_text(
                json.dumps(raw), encoding="utf-8")
        state = state_for(*raws)
        state["snapshots"] = tracking._snapshots(self.root, state)
        for snap in state["snapshots"]:
            cache(state, snap["reviews"]["1"], observation(QUOTE))
        latest = state["snapshots"][-1]["reviews"]["1"]
        self.expected_versions = [snap["reviews"]["1"]["review_version_id"] for snap in state["snapshots"]]
        self.expected_observations = [tracking.observations(state, snap["reviews"]["1"])[0]["observation_id"]
                                      for snap in state["snapshots"]]
        self.expected_counts = [tracking._stats(state, snap) for snap in state["snapshots"]]
        saved = tracking.observations(state, latest)[0]
        source_path = self.data_dir / "chatbi-source.json"
        source_path.write_text(json.dumps({
            "snapshot": {"source_url": "https://store.steampowered.com/app/94000"},
            "reviews": [{"review_id": "1", "text": TEXTS[1], "language": "english", "recommended": True,
                         "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-02T00:00:00Z"}],
        }), encoding="utf-8")
        run = {"source": {"file": str(source_path)}, "observations": [saved],
               "issues": [dict(state["issues"]["issue"], observation_ids=[saved["observation_id"]], review_ids=["1"])],
               "summary": {"reviews": 1, "observations": 1, "final_issues": 1, "none_reviews": 0, "failed_reviews": 0},
               "category_issue_counts": {"stability": 1}, "category_observation_counts": {"stability": 1},
               "created_at": "2026-09-20T00:00:00Z"}
        runs = self.root / "evaluation/dinner-date-full-run"
        runs.mkdir(parents=True)
        (runs / "run-20260920.json").write_text(json.dumps(run), encoding="utf-8")
        if pending:
            state["cache"].pop(tracking._cache_key(latest))
        tracking._save(self.root, state)
        return state

    def lookup(self, root, text, language="unknown", context=""):
        value = self.translated.get((text, language, context))
        return {"text_zh": value, "status": "ready" if value is not None else "pending"}

    def translate(self, root, items, progress=lambda message: None):
        self.translation_calls.append(deepcopy(items))
        self.events.append("translation")
        for item in items:
            key = (item["text"], item.get("language", "unknown"), item.get("context", ""))
            self.translated[key] = "中文：" + item["text"]
        self.revision += 1
        return {"total": len(items), "ready": len(items), "failed": 0, "calls": 0}

    @contextmanager
    def translation_cache(self):
        with patch.object(translation, "lookup", side_effect=self.lookup), \
                patch.object(translation, "revision", side_effect=lambda root: self.revision), \
                patch.object(translation, "translate", side_effect=self.translate):
            yield

    @contextmanager
    def server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        with patch.object(app, "ROOT", self.root), patch.object(app, "PORT", server.server_port), \
                patch.object(app.p, "deepseek", side_effect=AssertionError("GET attempted a model call")), \
                patch.object(app.p, "urlopen", side_effect=AssertionError("GET attempted remote HTTP")), \
                patch.object(app, "collect", side_effect=AssertionError("GET attempted collection")), \
                patch.object(translation, "translate", side_effect=AssertionError("GET attempted translation")):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def get(self, base, path):
        with urlopen(base + path, timeout=10) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)["data"]

    def assert_identity_and_counts(self, state):
        self.assertEqual([snap["reviews"]["1"]["review_version_id"] for snap in state["snapshots"]], self.expected_versions)
        self.assertEqual([tracking.observations(state, snap["reviews"]["1"])[0]["observation_id"]
                          for snap in state["snapshots"]], self.expected_observations)
        self.assertEqual([tracking._stats(state, snap) for snap in state["snapshots"]], self.expected_counts)
        self.assertEqual(set(state["assignments"].values()), {"issue"})

    def test_live_pipeline_translates_full_reviews_before_extracting_and_quotes_afterward(self):
        self.fixture(pending=True)
        def model(prompt, payload, validator, audit, attempts=2, model=None):
            self.events.append("extraction")
            self.assertEqual(prompt, flow.OBSERVATION_PROMPT)
            self.assertEqual(model, tracking.MODEL)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["text"], TEXTS[1])
            for text in TEXTS:
                self.assertEqual(self.lookup(self.root, text, "english")["status"], "ready")
            result = validator(json.dumps({"reviews": [{"id": payload[0]["id"],
                                  "categories": [{"category": "stability", "confidence": 1}],
                                  "observations": [observation(QUOTE)]}]}))
            audit({"model": model, "status": "passed", "usage": {"total_tokens": 1}})
            return result
        with self.translation_cache(), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), \
                patch.object(tracking.pipeline, "deepseek", side_effect=model), \
                patch.object(tracking.pipeline, "urlopen", side_effect=AssertionError("unexpected network")):
            state = tracking.refresh(self.root, live=True)
            self.assertLess(self.events.index("translation"), self.events.index("extraction"))
            self.assertEqual(self.events[-1], "translation")
            quote_requests = [row for rows in self.translation_calls for row in rows if row["text"] == QUOTE]
            self.assertEqual({row["context"] for row in quote_requests}, set(TEXTS))
            self.assertEqual({row["language"] for row in quote_requests}, {"english"})
            for snap in state["snapshots"]:
                version = snap["reviews"]["1"]
                self.assertEqual(version["text_zh"], "中文：" + version["text"])
                self.assertEqual(tracking.observations(state, version)[0]["evidence_zh"], "中文：" + QUOTE)
            self.assert_identity_and_counts(state)
            with patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("repeated extraction")):
                repeated = tracking.refresh(self.root, live=True)
            self.assert_identity_and_counts(repeated)

    def test_full_translation_failure_stops_extraction_and_preserves_originals(self):
        self.fixture(pending=True)
        original_files = {path: path.read_bytes() for path in self.data_dir.glob("steam-*.json")}
        def failed(root, items, progress=lambda message: None):
            return {"total": len(items), "ready": 0, "failed": len(items), "calls": 0}
        with self.translation_cache(), patch.object(translation, "translate", side_effect=failed), \
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), \
                patch.object(flow, "extract_versions", side_effect=AssertionError("translation failed before extraction")) as extract:
            with self.assertRaises(ValueError):
                tracking.refresh(self.root, live=True)
            extract.assert_not_called()
            saved = tracking._read(tracking._path(self.root))
            self.assertEqual([snap["reviews"]["1"]["text"] for snap in saved["snapshots"]], list(TEXTS))
            self.assertEqual([snap["reviews"]["1"]["review_version_id"] for snap in saved["snapshots"]], self.expected_versions)
        self.assertTrue(all(path.read_bytes() == original for path, original in original_files.items()))

    def test_translation_backfill_never_reextracts_or_changes_ids_and_counts(self):
        self.fixture()
        with self.translation_cache(), \
                patch.object(flow, "extract_versions", side_effect=AssertionError("translation backfill re-extracted")), \
                patch.object(tracking.pipeline, "deepseek", side_effect=AssertionError("backfill called analysis model")):
            tracking.refresh_translations(self.root)
            state = tracking.history(self.root)
            self.assert_identity_and_counts(state)
            self.assertTrue(all(snap["reviews"]["1"]["text_zh"] for snap in state["snapshots"]))

    def test_dinner_date_backfill_discovers_capture_missing_from_saved_history(self):
        original = self.fixture()
        capture = envelope("untranslated-capture", 30, [review("2", "A newly collected review.")])
        with patch.object(translation, "translate", return_value={"failed": 1}):
            with self.assertRaises(ValueError):
                save_capture(self.root, capture)
        with self.translation_cache(), \
                patch.object(flow, "extract_versions", side_effect=AssertionError("backfill re-extracted")), \
                patch.object(app.p, "deepseek", side_effect=AssertionError("backfill called analysis model")):
            self.assertEqual(len(tracking.history(self.root)["snapshots"]), 2)
            translate_game("dinner-date", progress=lambda message: None)
            updated = tracking.history(self.root)
            self.assertEqual(len(updated["snapshots"]), 3)
            newest = updated["snapshots"][-1]
            self.assertEqual(newest["id"], "untranslated-capture")
            self.assertEqual(newest["reviews"]["2"]["text_zh"], "中文：A newly collected review.")
            self.assertIsNone(tracking.observations(updated, newest["reviews"]["2"]))
            self.assert_identity_and_counts(dict(updated, snapshots=updated["snapshots"][:2]))
            for key in ("analysis_version", "cache", "assignments", "issues"):
                self.assertEqual(original[key], updated[key])

    def test_ostranauts_backfill_refreshes_read_cache_without_reanalysis(self):
        source = envelope("ostranauts-a", 20, [review(text=TEXTS[1]), review("2", "Great game.")])
        source["snapshot"]["app_id"] = 1022980
        source_path = self.data_dir / "ostranauts-source.json"
        source_path.write_text(json.dumps(source), encoding="utf-8")
        version = dict(source["reviews"][0], app_id=1022980)
        version_id = app.p.fingerprint(version)[:24]
        item = observation(QUOTE)
        oid = app.p.fingerprint([version_id, item])[:24]
        record = {"text": TEXTS[1], "categories": [{"category": "stability", "confidence": 1}], "observations": [item]}
        raw = {"source": {"file": str(source_path)}, "records": {"1": record},
               "observations": [dict(item, observation_id=oid, review_id="1")],
               "issues": [{"issue_id": "ostranauts-issue", "title": "游戏崩溃", "category": "stability",
                           "canonical_description": "游戏启动时崩溃", "observation_ids": [oid], "review_ids": ["1"]}],
               "category_issue_counts": {"stability": 1}, "category_observation_counts": {"stability": 1},
               "summary": {"reviews": 1, "observations": 1, "final_issues": 1, "none_reviews": 0, "failed_reviews": 0},
               "created_at": "2026-09-20T00:00:00Z"}
        folder = app.GAMES["ostranauts"]["folder"]
        folder.mkdir(parents=True)
        product = folder / "product-test.json"
        product.write_text(json.dumps(raw), encoding="utf-8")
        originals = [path.read_bytes() for path in (source_path, product)]
        with self.translation_cache(), \
                patch.object(flow, "extract_versions", side_effect=AssertionError("backfill re-extracted")), \
                patch.object(app.p, "deepseek", side_effect=AssertionError("backfill called analysis model")):
            before = app.product_state("ostranauts")
            self.assertIsNone(before["snapshots"][0]["reviews"]["1"]["text_zh"])
            results = translate_game("ostranauts", progress=lambda message: None)
            self.assertEqual(list(results), ["analyzed_reviews", "excerpts", "reviews"])
            self.assertEqual([len(rows) for rows in self.translation_calls], [1, 1, 2])
            self.assertEqual(self.translation_calls[1], [{"text": QUOTE, "language": "english", "context": TEXTS[1]}])
            after = app.product_state("ostranauts")
            self.assertNotEqual(before["translation_revision"], after["translation_revision"])
            for state in (before, after):
                snap = state["snapshots"][0]
                self.assertEqual(snap["reviews"]["1"]["review_version_id"], version_id)
                self.assertEqual(tracking.observations(state, snap["reviews"]["1"])[0]["observation_id"], oid)
            for key in ("analysis_version", "cache", "assignments", "issues"):
                self.assertEqual(before[key], after[key])
            self.assertEqual(tracking._stats(before, before["snapshots"][0]), tracking._stats(after, after["snapshots"][0]))
            self.assertTrue(all(row["text_zh"] for row in after["snapshots"][0]["reviews"].values()))
            self.assertEqual(tracking.observations(after, after["snapshots"][0]["reviews"]["1"])[0]["evidence_zh"], "中文：" + QUOTE)
            with self.server() as base:
                evidence = self.get(base, "/api/chatbi/issues/ostranauts-issue/evidence?game=ostranauts")
                self.assertEqual(evidence[0]["quote_zh"], "中文：" + QUOTE)
                history = self.get(base, "/api/tracking/reviews/1?game=ostranauts")
                self.assertEqual(history["history"][0]["text_zh"], "中文：" + TEXTS[1])
        self.assertEqual([path.read_bytes() for path in (source_path, product)], originals)

    def test_collection_without_analysis_saves_originals_then_translates(self):
        snapshot = dict(envelope("capture", 20, [review(text=TEXTS[0])]), collection={"count": 1})
        def translate(root, items, progress=lambda message: None):
            saved = list(self.data_dir.glob("steam-*.json"))
            self.assertTrue(saved)
            self.assertEqual(json.loads(saved[-1].read_text(encoding="utf-8")), snapshot)
            return self.translate(root, items, progress)
        with self.server() as base, patch.object(app, "collect", return_value=snapshot), \
                patch.object(translation, "translate", side_effect=translate), \
                patch.object(app, "JOB_LOCK", threading.Lock()), \
                patch.object(app, "TRACKING_JOB", {}), patch.object(tracking, "refresh") as refresh:
            app.JOB_LOCK.acquire()
            app.refresh_tracking(True, False)
            self.assertEqual(app.TRACKING_JOB["status"], "complete")
            self.assertEqual(refresh.call_args.kwargs["live"], False)
            self.assertEqual(len(self.translation_calls), 1)
            request = Request(base + "/api/collect", data=json.dumps({"app_id": 94000, "pages": 1}).encode(),
                              headers={"Content-Type": "application/json", "X-InsightPulse-Token": app.TOKEN}, method="POST")
            with urlopen(request, timeout=10) as response:
                self.assertEqual(json.load(response)["count"], 1)
            self.assertEqual(len(self.translation_calls), 2)
            self.assertEqual(len(list(self.data_dir.glob("steam-*.json"))), 2)
            self.assertEqual(refresh.call_count, 1)

    def test_failed_capture_translation_preserves_raw_snapshot(self):
        snapshot = envelope("capture", 20, [review(text=TEXTS[0])])
        with patch.object(translation, "translate", return_value={"failed": 1}):
            with self.assertRaisesRegex(ValueError, "原文已保存.*无需重新采集"):
                save_capture(self.root, snapshot)
        paths = list(self.data_dir.glob("steam-*.json"))
        self.assertEqual(len(paths), 1)
        self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8")), snapshot)

    def test_http_bilingual_evidence_refreshes_on_translation_revision_without_model_calls(self):
        self.fixture()
        def lookup(root, text, language="unknown", context=""):
            return {"text_zh": f"译文{self.revision}：{text}", "status": "ready"}
        with patch.object(translation, "lookup", side_effect=lookup), \
                patch.object(translation, "revision", side_effect=lambda root: self.revision), self.server() as base:
            first = self.get(base, "/api/tracking/compare?before=a&after=b")
            detail = self.get(base, "/api/chatbi/issues/issue")
            overview = self.get(base, "/api/chatbi/overview")
            evidence = self.get(base, "/api/chatbi/issues/issue/evidence")
            for item in (detail["evidence_items"][0], detail["player_themes"][0]["evidence_items"][0],
                         overview["featured_evidence"][0]["evidence"], evidence[0]):
                self.assertEqual(item["quote"], QUOTE)
                self.assertEqual(item["quote_zh"], "译文1：" + QUOTE)
            tools = app.chat_tools({"tool": {"name": "get_issue_evidence", "category": "", "limit": 1},
                                    "evidence_issue_ids": ["issue"]}, app.chatbi_snapshot())
            citation = next(tool["result"] for tool in tools if tool["name"] == "show_citation")
            self.assertEqual(citation["snippet"], QUOTE)
            self.assertEqual(citation["snippet_zh"], "译文1：" + QUOTE)
            history = self.get(base, "/api/tracking/reviews/1")
            self.assertEqual([item["text"] for item in history["history"]], list(TEXTS))
            self.assertEqual([item["text_zh"] for item in history["history"]], ["译文1：" + text for text in TEXTS])
            self.assertFalse(first["comparable"])
            self.revision = 2
            second = self.get(base, "/api/tracking/compare?before=a&after=b")
            changed_detail = self.get(base, "/api/chatbi/issues/issue")
            changed_history = self.get(base, "/api/tracking/reviews/1")
            self.assertEqual(changed_history["history"][-1]["text_zh"], "译文2：" + TEXTS[1])
            self.assertEqual(changed_detail["evidence_items"][0]["quote_zh"], "译文2：" + QUOTE)
            self.assertEqual(first["issues"], second["issues"])
            self.assertEqual(detail["id"], changed_detail["id"])
            self.assertEqual(detail["reviews"], changed_detail["reviews"])
            self.assertEqual(detail["evidence_items"][0]["observation_id"], changed_detail["evidence_items"][0]["observation_id"])

    def test_change_evidence_has_its_own_translation_status(self):
        state = self.fixture()
        version = state["snapshots"][-1]["reviews"]["1"]
        state["cache"][tracking._cache_key(version)]["observations"][0]["change_evidence"] = "After update"
        def lookup(root, text, language="unknown", context=""):
            return ({"text_zh": "更新后", "status": "ready"} if text == "After update"
                    else {"text_zh": None, "status": "failed"})
        with patch.object(translation, "lookup", side_effect=lookup):
            tracking._attach_translations(state, self.root)
        item = tracking.observations(state, version)[0]
        self.assertEqual(item["translation_status"], "failed")
        self.assertEqual(item["change_translation_status"], "ready")
        self.assertEqual(item["change_evidence_zh"], "更新后")

    def test_missing_translation_keeps_original_review_and_evidence_visible(self):
        self.fixture()
        with patch.object(translation, "lookup", return_value={"text_zh": None, "status": "pending"}), \
                patch.object(translation, "revision", return_value=0), self.server() as base:
            item = self.get(base, "/api/chatbi/issues/issue/evidence")[0]
            self.assertIsNone(item["quote_zh"])
            self.assertEqual(item["quote"], QUOTE)
            history = self.get(base, "/api/tracking/reviews/1")
            self.assertEqual([item["text"] for item in history["history"]], list(TEXTS))
            self.assertTrue(all(item["text_zh"] is None for item in history["history"]))
            self.assertTrue(all(item["observations"][0]["evidence"] == QUOTE for item in history["history"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
