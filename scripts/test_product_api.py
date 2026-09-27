"""Runnable stdlib checks for the productized ChatBI API."""
import json
import os
from datetime import datetime
from http.server import ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app


class ProductApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_port = app.PORT
        cls.old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        app.MODEL_STATE.update(status="missing", last_error="")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        app.PORT = cls.server.server_port
        cls.base = f"http://127.0.0.1:{app.PORT}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        app.PORT = cls.old_port
        if cls.old_key is not None:
            os.environ["DEEPSEEK_API_KEY"] = cls.old_key

    def get(self, path):
        with urlopen(self.base + path) as response:
            return response.status, json.load(response)["data"]

    def post(self, path, body):
        request = Request(self.base + path, method="POST", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json", "X-InsightPulse-Token": app.TOKEN})
        with urlopen(request) as response:
            return response.status, json.load(response)["data"]

    def test_read_endpoints_use_real_snapshot(self):
        _, overview = self.get("/api/chatbi/overview")
        self.assertEqual(830, overview["metrics"]["reviews"])
        self.assertEqual(56, overview["metrics"]["issues"])
        self.assertEqual(3, len(overview["featured_evidence"]))
        self.assertTrue(overview["featured_evidence"][0]["evidence"]["quote"])
        _, issues = self.get("/api/chatbi/issues?limit=2")
        self.assertEqual(2, len(issues["items"]))
        self.assertNotIn("evidence_items", issues["items"][0])
        self.assertNotIn("player_themes", issues["items"][0])
        self.assertEqual(issues["items"][0]["reviews"], len(issues["items"][0]["review_ids"]))
        _, detail = self.get("/api/chatbi/issues/" + issues["items"][0]["id"])
        self.assertTrue(detail["evidence_items"])
        self.assertEqual(3, len(detail["player_themes"]))
        self.assertEqual(detail["reviews"], detail["evidence_capsule"]["review_count"])
        self.assertEqual(detail["observations"], detail["evidence_capsule"]["observation_count"])
        self.assertTrue(detail["evidence_capsule"]["manifestations"])
        self.assertEqual(detail["observations"], sum(len(item["observation_ids"]) for item in detail["evidence_capsule"]["manifestations"]))
        _, evidence = self.get("/api/chatbi/issues/" + issues["items"][0]["id"] + "/evidence")
        self.assertEqual(detail["evidence_items"], evidence)
        _, settings = self.get("/api/settings")
        self.assertEqual("DeepSeek", settings["provider"])
        with self.assertRaises(HTTPError) as caught:
            self.get("/api/chatbi/experiments")
        self.assertEqual(404, caught.exception.code)
        caught.exception.close()

    def test_issue_fields_preserve_real_counts_and_evidence(self):
        snapshot = app.chatbi_snapshot()
        raw = json.loads(app.chatbi_source().read_text(encoding="utf-8"))
        raw_issues = {item["issue_id"]: item for item in raw["issues"]}
        observations = {item["observation_id"]: item for item in raw["observations"]}
        for issue in snapshot["issues"]:
            source = raw_issues[issue["id"]]
            self.assertEqual(set(source["review_ids"]), set(issue["review_ids"]))
            self.assertEqual(issue["reviews"], len(set(issue["review_ids"])))
            self.assertEqual(issue["observations"], sum(issue["claim_counts"].values()))
            for theme in issue["player_themes"]:
                matching = [observations[oid] for oid in source["observation_ids"]
                            if (observations[oid].get("object", ""), observations[oid].get("symptom", ""),
                                observations[oid].get("claim", "unknown"))
                            == (theme["object"], theme["symptom"], theme["claim"])]
                self.assertEqual(len({item["review_id"] for item in matching}), theme["reviews"])
                self.assertEqual(len(matching), theme["observations"])
                self.assertIn(theme["title"], [item["description"] for item in matching])
                for evidence in theme["evidence_items"]:
                    observation = observations[evidence["observation_id"]]
                    self.assertIn(evidence["observation_id"], source["observation_ids"])
                    self.assertEqual(observation["evidence"], evidence["quote"])
                    self.assertEqual(observation["review_id"], evidence["review_id"])
                    self.assertEqual(snapshot["source_url"], evidence["url"])
        gameplay = [item for item in snapshot["issues"] if item["category"] == "gameplay_mechanics"]
        unique_reviews = {rid for item in gameplay for rid in item["review_ids"]}
        self.assertLess(len(unique_reviews), sum(item["reviews"] for item in gameplay))
        positive = next(item for item in snapshot["issues"]
                        if item["title"] == "最新版本运行帧率提升至144Hz且超过30fps")
        self.assertEqual({"improved": 1}, positive["claim_counts"])

    def test_invalid_issue_has_structured_404(self):
        with self.assertRaises(HTTPError) as caught:
            self.get("/api/chatbi/issues/not-real")
        self.assertEqual(404, caught.exception.code)
        error = json.load(caught.exception)["error"]
        caught.exception.close()
        self.assertEqual("ISSUE_NOT_FOUND", error["code"])

    def test_settings_and_missing_key(self):
        _, settings = self.post("/api/settings", {"model": "deepseek-v4-flash", "key": ""})
        self.assertFalse(settings["configured"])
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/chat", {"question": "最高频的问题是什么？"})
        self.assertEqual(409, caught.exception.code)
        error = json.load(caught.exception)["error"]
        caught.exception.close()
        self.assertEqual("API_KEY_MISSING", error["code"])

    def test_chat_validation_rejects_untrusted_issue_ids(self):
        snapshot = app.chatbi_snapshot()
        payload = {"answer": "查看重点问题", "tool": {"name": "get_overview", "limit": 5},
                   "evidence_issue_ids": ["not-real"], "caveat": ""}
        with self.assertRaisesRegex(ValueError, "证据引用"):
            app.validate_chatbi_answer(json.dumps(payload, ensure_ascii=False), snapshot)

    def test_decision_briefs_keep_facts_local_and_evidence_traceable(self):
        snapshot = app.chatbi_snapshot()
        candidates = app.decision_candidates(snapshot)
        capsules = app.decision_capsules(snapshot, candidates)
        gates = app.decision_gates(candidates, capsules)
        allowed = {issue_id: {representative["observation_id"] for manifestation in capsule["manifestations"]
                   for representative in manifestation["representatives"]}
                   for issue_id, capsule in capsules.items()}
        content = {"decisions": [{"issue_id": issue["id"], "type": "investigate",
                    "title": "核查问题边界", "action": "先复现玩家遇到的具体困难并确认最小改动范围",
                    "unknown": "目前缺少产品内行为数据与实施成本",
                    "validation_focus": "确认改动是否减少相同用户困难",
                    "basis_dimensions": ["coverage", "concentration"],
                    "rationale": "现有证据支持先核查问题边界和未知条件",
                    "evidence_observation_ids": [next(iter(allowed[issue["id"]]))]} for issue in candidates]}
        drafts = app.validate_decision_drafts(json.dumps(content, ensure_ascii=False), candidates, allowed, gates)
        result = app.decision_response(snapshot, drafts, capsules, gates)
        self.assertEqual("ready", result["status"])
        self.assertEqual("evidence-capsule-v1.2", result["runtime_metrics"]["capsule_version"])
        self.assertTrue(all("evidence" not in item for item in app.decision_payload(candidates, capsules)))
        self.assertEqual({issue["id"] for issue in candidates},
                         {decision["issue"]["id"] for decision in result["decisions"]})
        for decision in result["decisions"]:
            issue = next(item for item in candidates if item["id"] == decision["issue"]["id"])
            self.assertEqual(f"{issue['reviews']} 条去重评论", decision["facts"][0]["value"])
            self.assertEqual(len({item["review_id"] for item in decision["evidence"]}), len(decision["evidence"]))
            self.assertEqual([item["observation_id"] for item in decision["evidence"]],
                             [item["representative_observation_id"] for item in decision["evidence_links"]])
            self.assertEqual("decision-gate-v1", decision["gate"]["version"])
            self.assertEqual(content["decisions"][0]["rationale"], decision["rationale"])
            self.assertIn("待验证", decision["judgment"])
        stored = {app.decision_cache_key(snapshot): result}
        with patch.object(app, "cache_get", side_effect=stored.get), \
                patch.object(app, "cache_put", side_effect=lambda key, value: stored.__setitem__(key, value)):
            reviewed = app.review_decision(snapshot, result["decisions"][0]["id"], "accepted")
        self.assertEqual("accepted", reviewed["decisions"][0]["review"]["status"])
        self.assertEqual(reviewed["decisions"][0]["action"], reviewed["decisions"][0]["review"]["final_action"])
        with patch.object(app, "cache_get", side_effect=stored.get), \
                patch.object(app, "cache_put", side_effect=lambda key, value: stored.__setitem__(key, value)):
            followed = app.update_decision_outcome(snapshot, result["decisions"][0]["id"], "完成一轮用户测试", {
                "status": "shipped", "shipped_at": "2026-09-26T10:00:00+08:00"}, {
                "hypothesis": "明确交互反馈后困惑会减少", "metric": "新增评论中的问题提及占比",
                "baseline": "当前 12.4%", "followup_window": "下个版本后 30 条评论",
                "observed_result": "下降至 6.1%", "outcome": "improved"})
        self.assertEqual("shipped", followed["decisions"][0]["execution"]["status"])
        self.assertEqual("improved", followed["decisions"][0]["validation"]["outcome"])
        self.assertEqual("decision-outcome-v1", followed["decisions"][0]["outcome_tracking"]["version"])
        with patch.object(app, "cache_get", side_effect=stored.get), \
                patch.object(app, "cache_put", side_effect=lambda key, value: stored.__setitem__(key, value)):
            reviewed = app.review_decision(snapshot, result["decisions"][0]["id"], "modified", "需缩小验证范围")
        self.assertEqual("需缩小验证范围", reviewed["decisions"][0]["review"]["reason"])
        with patch.object(app, "cache_get", side_effect=stored.get), \
                patch.object(app, "cache_put", side_effect=lambda key, value: stored.__setitem__(key, value)):
            with self.assertRaisesRegex(ValueError, "需要填写原因"):
                app.review_decision(snapshot, result["decisions"][0]["id"], "rejected")
        content["decisions"][0]["issue_id"] = "not-real"
        with self.assertRaises(ValueError):
            app.validate_decision_drafts(json.dumps(content, ensure_ascii=False), candidates, allowed, gates)

    def test_decision_gate_blocks_direct_action_for_mixed_manifestations(self):
        issue = {"reviews": 12, "observations": 12}
        capsule = {"manifestations": [{"id": "main"}, {"id": "minority"}], "evidence_quality": {
            "largest_manifestation_share": 0.8, "specific_observation_share": 1.0,
            "minority_manifestations": 1, "contradictory_review_count": 0}}
        gate = app.decision_gate(issue, capsule)
        self.assertNotIn("act", gate["allowed_actions"])
        self.assertIn("investigate", gate["allowed_actions"])
        capsule["manifestations"] = [{"id": "main"}]
        capsule["evidence_quality"].update(largest_manifestation_share=1.0, minority_manifestations=0)
        self.assertIn("act", app.decision_gate(issue, capsule)["allowed_actions"])

    def test_issue_detail_tools_follow_the_selected_evidence(self):
        snapshot = app.chatbi_snapshot()
        selected = snapshot["issues"][-1]
        plan = app.validate_chatbi_answer(json.dumps({
            "answer": "核查当前问题", "tool": {"name": "get_issue_detail", "category": "", "limit": 5},
            "evidence_issue_ids": [selected["id"]], "caveat": "",
        }, ensure_ascii=False), snapshot)
        result = app.chat_tools(plan, snapshot)
        table = next(tool["result"] for tool in result if tool["name"] == "show_table")
        chart = next(tool["result"] for tool in result if tool["name"] == "show_chart")
        self.assertEqual([selected["id"]], [row["id"] for row in table["data"]])
        self.assertEqual([{ "issue": selected["title"], "reviews": selected["reviews"] }], chart["data"])
        self.assertEqual("当前问题", chart["title"])
        citation = next(tool["result"] for tool in result if tool["name"] == "show_citation")
        self.assertTrue(citation["publishedAt"].endswith("Z"))
        self.assertEqual(datetime.fromisoformat(selected["evidence_items"][0]["created_at"]),
                         datetime.fromisoformat(citation["publishedAt"]))

    def test_product_and_assistant_omit_unconfigured_issue_rules(self):
        snapshot = app.chatbi_snapshot()
        _, issues = self.get("/api/chatbi/issues")
        _, overview = self.get("/api/chatbi/overview")
        _, detail = self.get("/api/chatbi/issues/" + issues["items"][0]["id"])
        plan = {"answer": "查看问题", "tool": {"name": "rank_issues", "category": "", "limit": 5},
                "evidence_issue_ids": [], "caveat": ""}
        with patch.object(app.p, "deepseek", return_value=plan) as model, patch.dict(app.MODEL_STATE):
            response = app.ask_chatbi("哪些问题关联评论较多？")
        payload = model.call_args.args[1]
        table = next(tool["result"] for tool in response["tools"] if tool["name"] == "show_table")
        removed = {"priority", "status", "recommendation"}
        for issue in [*snapshot["issues"], *issues["items"], *overview["top_issues"], detail,
                      *(item["issue"] for item in overview["featured_evidence"]),
                      *payload["issues"], *table["data"]]:
            self.assertFalse(removed.intersection(issue), issue["id"])
        self.assertFalse(removed.intersection(column["key"] for column in table["columns"]))
        self.assertEqual({item["id"]: item["reviews"] for item in snapshot["issues"]},
                         {item["id"]: item["reviews"] for item in payload["issues"]})
        with self.assertRaises(HTTPError) as caught:
            self.get("/api/chatbi/issues?sort_by=priority")
        self.assertEqual(400, caught.exception.code)
        self.assertEqual("INVALID_REQUEST", json.load(caught.exception)["error"]["code"])
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
