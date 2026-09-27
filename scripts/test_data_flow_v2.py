"""Deterministic checks for snapshot history and game-update windows."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_flow_v2 as f
import pipeline as p

EVENT = 200_000
RULES = dict(min_window_reviews=1, min_issue_support=1, min_explicit_reports=1, change_pp=10)


def review(rid, text, created, updated=None, recommended=True):
    return dict(id=rid, text=text, created=created, updated=created if updated is None else updated,
                language="schinese", recommended=recommended)


def snapshot(at, rows, **extra):
    return dict(app_id=f.APP_ID, captured_at=at, reviews=rows, coverage="exhausted", **extra)


def obs(evidence, symptom="crash", claim="complaint", category=None):
    category = category or ("stability" if symptom == "crash" else "network_multiplayer")
    return dict(description="游戏崩溃" if symptom == "crash" else "匹配等待过长",
                category=category, evidence=evidence, object="game", symptom=symptom,
                claim=claim, change_evidence=evidence if claim != "complaint" else "")


def extraction(*observations):
    names = dict.fromkeys(item["category"] for item in observations)
    categories = [{"category": name, "confidence": 0.9} for name in names]
    return {"categories": categories or [{"category": "none", "confidence": 0.9}],
            "observations": list(observations)}


class DataFlowChecks(unittest.TestCase):
    def test_truncated_model_output_retries_with_more_room(self):
        import io
        requests = []
        def response(request, **kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "length" if len(requests) == 1 else "stop", "message": {"content": "complete"}}]}).encode())
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "offline-test"}), patch.object(p, "urlopen", side_effect=response), patch.object(p.time, "sleep"):
            self.assertEqual(p.deepseek("rules", [], lambda content: content, lambda call: None), "complete")
        self.assertEqual([r["max_tokens"] for r in requests], [8192, 16384])
        self.assertEqual(requests[0]["messages"], requests[1]["messages"])

    def test_model_retry_receives_validation_error(self):
        import io
        requests = []
        def response(request, **kwargs):
            requests.append(json.loads(request.data))
            content = "bad" if len(requests) == 1 else "good"
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode())
        def validate(content):
            if content == "bad":
                raise ValueError("unknown observation ID")
            return content
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "offline-test"}), patch.object(p, "urlopen", side_effect=response), patch.object(p.time, "sleep"):
            self.assertEqual(p.deepseek("rules", [], validate, lambda call: None), "good")
        self.assertEqual(requests[1]["messages"][-2], {"role": "assistant", "content": "bad"})
        self.assertIn("unknown observation ID", requests[1]["messages"][-1]["content"])

    def test_dense_review_retry_keeps_all_evidence_and_reuses_cache(self):
        text = "; ".join(f"crash case {i}" for i in range(9))
        data = f.load_snapshots([snapshot(300_000, [review("dense", text, 150_000)])])
        vid = next(iter(data["review_versions"]))
        value = extraction(*(dict(obs(f"crash case {i}"), object=f"scene_{i}") for i in range(9)))
        validated = f.validate_observations(json.dumps({"reviews": [dict(id=vid, **value)]}), [{"id": vid, "text": text}])
        with patch.object(f.legacy, "extract_batch", side_effect=[({}, {vid: "Review exceeds the observation limit"}), (validated, {})]) as model:
            result, failed, cached = f.extract_versions(data, [vid], "test-model")
        self.assertFalse(failed)
        self.assertEqual(len(result[vid]["observations"]), 9)
        self.assertTrue(model.call_args_list[1].args[0].startswith(f.LONG_REVIEW_PROMPT))
        self.assertNotIn("at most 8 observations", f.LONG_REVIEW_PROMPT)
        with patch.object(f.legacy, "extract_batch", side_effect=AssertionError("cache not reused")):
            self.assertEqual(f.extract_versions(data, [vid], "test-model", cached=cached)[0], result)
        invalid = dict(value, observations=[dict(value["observations"][0], evidence="invented quote")])
        with self.assertRaises(ValueError):
            f.validate_observations(json.dumps({"reviews": [dict(id=vid, **invalid)]}), [{"id": vid, "text": text}])

    def test_ostranauts_snapshots_keep_game_identity(self):
        other = dict(snapshot(300_000, [review("r", "saved original", 150_000)]), app_id=1022980)
        data = f.load_snapshots([other])
        self.assertEqual(data["app_id"], 1022980)
        self.assertTrue(all(row["app_id"] == 1022980 for row in data["review_versions"].values()))
        with self.assertRaises(ValueError):
            f.load_snapshots([other, snapshot(300_000, [])])

    def test_01_snapshot_metadata_is_not_repeated_on_review_versions(self):
        data = f.load_snapshots([snapshot(300_000, [review("1", "崩溃", 150_000)],
                                                 source_type="fixture", source_url="https://example.test")])
        version = next(iter(data["review_versions"].values()))
        self.assertEqual(set(version), {"app_id", "review_id", "review_version_id", "text",
                                        "created_at", "updated_at", "language", "recommended"})
        self.assertEqual(data["snapshots"][0]["source_type"], "fixture")
        self.assertNotIn("source_url", version)
        self.assertNotIn("sightings", version)

    def test_02_event_windows_use_created_at_not_capture_time(self):
        plan = f.build_plan([snapshot(300_000, [review("b", "好玩", 150_000),
                                                review("a", "崩溃", 220_000)])], EVENT, 1)
        self.assertEqual({r["review_id"]: r["window"] for r in plan["reviews"]},
                         {"a": "after_update", "b": "before_update"})

    def test_03_review_revision_is_separate_from_event_comparison(self):
        plan = f.build_plan([snapshot(180_000, [review("r", "匹配慢", 150_000)]),
                             snapshot(300_000, [review("r", "后来崩溃", 150_000, 250_000)])], EVENT, 1)
        self.assertEqual(plan["review_revisions"][0]["change_type"], "review_revision")
        self.assertEqual(len(plan["reviews"]), 1)
        chosen = plan["review_versions"][plan["reviews"][0]["review_version_id"]]
        self.assertEqual(chosen["text"], "匹配慢")
        self.assertEqual(plan["reviews"][0]["window"], "before_update")

    def test_04_text_difference_without_updated_at_is_not_player_revision(self):
        data = f.load_snapshots([snapshot(180_000, [review("r", "word", 150_000)]),
                                 snapshot(300_000, [review("r", "♥♥♥♥", 150_000)])])
        changes, conflicts = f.review_history(data)
        self.assertFalse(conflicts)
        self.assertEqual(changes[0]["change_type"], "source_text_changed")

    def test_05_metadata_change_and_unchanged_are_explicit(self):
        changed = f.load_snapshots([snapshot(180_000, [review("r", "same", 150_000)]),
                                    snapshot(300_000, [review("r", "same", 150_000, recommended=False)])])
        same = f.load_snapshots([snapshot(180_000, [review("r", "same", 150_000)]),
                                 snapshot(300_000, [review("r", "same", 150_000)])])
        self.assertEqual(f.review_history(changed)[0][0]["change_type"], "metadata_changed")
        self.assertEqual(f.review_history(same)[0][0]["change_type"], "snapshot_unchanged")

    def test_06_issue_metrics_compare_two_review_populations(self):
        plan = f.build_plan([snapshot(300_000, [review("b", "好玩", 150_000),
                                                review("a", "崩溃", 220_000)])], EVENT, 1)
        outputs = {r["review_version_id"]: (extraction(obs("崩溃")) if r["review_id"] == "a" else extraction())
                   for r in plan["reviews"]}
        result = f.assemble(plan, outputs, rules=RULES)
        metric = result["metrics"][0]
        self.assertEqual((metric["before_total"], metric["after_total"]), (1, 1))
        self.assertEqual(metric["status"], "new")
        self.assertNotIn("transitions", result)
        self.assertNotIn("source_type", result["observations"][0])
        self.assertNotIn("issue_text", result["observations"][0])
        self.assertEqual(result["issue_registry"]["issues"][0]["category"], "stability")

    def test_07_late_revision_cannot_represent_the_before_window(self):
        plan = f.build_plan([snapshot(300_000, [review("r", "后来崩溃", 150_000, 250_000)])], EVENT, 1)
        self.assertFalse(plan["reviews"])
        self.assertEqual(plan["excluded"]["no_saved_state_from_window"], 1)

    def test_08_repeated_sighting_is_analyzed_once(self):
        row = review("r", "崩溃", 220_000)
        plan = f.build_plan([snapshot(250_000, [row]), snapshot(300_000, [copy.deepcopy(row)])], EVENT, 1)
        self.assertEqual(len(plan["reviews"]), 1)
        self.assertEqual(len(plan["review_versions"]), 1)

    def test_09_extraction_cache_is_keyed_by_review_text(self):
        plan = f.build_plan([snapshot(300_000, [review("a", "崩溃", 220_000)])], EVENT, 1)
        requests = []

        def model(prompt, batch, audit, row_validator, model):
            requests.append(batch)
            content = json.dumps({"reviews": [{"id": batch[0]["id"], **extraction()}]})
            return row_validator(content, batch), {}

        with patch.object(p, "extract_batch", side_effect=model):
            output, failed, cache = f.extract_versions(plan, set(plan["review_versions"]), "fixture")
        self.assertFalse(failed)
        with patch.object(p, "extract_batch", side_effect=AssertionError("cache miss")):
            output2, failed2, _ = f.extract_versions(plan, set(plan["review_versions"]), "fixture", cache)
        self.assertEqual((output, failed), (output2, failed2))
        self.assertEqual(len(requests), 1)

    def test_10_invalid_change_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            f.validate_observations(json.dumps({"reviews": [{"id": "r", **extraction(obs("伪造", claim="improved"))}]}),
                                    [{"id": "r", "text": "好玩"}])

    def test_11_issue_id_and_registry_are_stable(self):
        plan = f.build_plan([snapshot(300_000, [review("b", "崩溃", 150_000),
                                                review("a", "崩溃", 220_000)])], EVENT, 1)
        outputs = {r["review_version_id"]: extraction(obs("崩溃")) for r in plan["reviews"]}
        first = f.assemble(plan, outputs, rules=RULES)
        second = f.assemble(plan, outputs, registry=first["issue_registry"], rules=RULES)
        self.assertEqual(first["issue_registry"], second["issue_registry"])
        self.assertEqual(first["metrics"], second["metrics"])

    def test_12_old_revision_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            f.status_rules(dict(min_new_mentions=2))
        with self.assertRaises(ValueError):
            f.load_snapshots([dict(snapshot(300_000, []), app_id=553850)])
        for invalid in ({"min_window_reviews": None}, {"min_issue_support": 1.5}, {"change_pp": -1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                f.status_rules(invalid)

    def test_13_category_schema_covers_single_multi_none_other_and_multiple_observations(self):
        controls = dict(obs("按键没有反应", symptom="input_has_no_effect", category="controls_input"),
                        description="按键不会影响角色反应", severity="critical")
        visual = dict(obs("手指模型异常", symptom="bad_hand_model", category="visual_graphics"),
                      description="手指模型显示异常")
        same_controls = dict(controls, evidence="按键不改变结果", description="按键操作不会改变结果")
        unusual = dict(obs("存档时间戳显示错位", symptom="save_timestamp_misaligned", category="other"),
                       description="存档时间戳显示错位")
        rows = [
            {"id": "single", **extraction(controls)},
            {"id": "multi", **extraction(controls, visual)},
            {"id": "dedup", **extraction(controls, same_controls)},
            {"id": "none", **extraction()},
            {"id": "empty", "categories": [], "observations": []},
            {"id": "other", **extraction(unusual)},
        ]
        reviews = [{"id": row["id"], "text": {
            "single": "按键没有反应", "multi": "按键没有反应，而且手指模型异常",
            "dedup": "按键没有反应，按键不改变结果", "none": "很喜欢这款游戏",
            "empty": "很喜欢这款游戏", "other": "存档时间戳显示错位",
        }[row["id"]]} for row in rows]
        result = f.validate_observations(json.dumps({"reviews": rows}, ensure_ascii=False), reviews)
        self.assertEqual(result["single"]["categories"][0]["category"], "controls_input")
        self.assertNotIn("severity", result["single"]["observations"][0])
        self.assertEqual(len(result["multi"]["observations"]), 2)
        self.assertEqual(len(result["dedup"]["observations"]), 1)
        self.assertEqual(result["none"]["observations"], [])
        self.assertEqual(result["empty"]["categories"], [{"category": "none", "confidence": 0.0}])
        self.assertEqual(result["other"]["observations"][0]["category"], "other")

    def test_14_observation_must_reference_a_selected_category(self):
        item = obs("手指模型异常", symptom="bad_hand_model", category="visual_graphics")
        payload = {"reviews": [{"id": "r", "categories": [{"category": "controls_input", "confidence": 0.9}],
                                "observations": [item]}]}
        with self.assertRaises(ValueError):
            f.validate_observations(json.dumps(payload, ensure_ascii=False), [{"id": "r", "text": "手指模型异常"}])

    def test_15_semantic_issue_grouping_uses_membership_not_exact_facets(self):
        observations = [
            dict(obs("finger model weird", symptom="rendering_abnormal", category="visual_graphics"),
                 observation_id="o1", review_id="r1", object="character_hand_model"),
            dict(obs("hand shader wrong", symptom="shader_wrong", category="visual_graphics"),
                 observation_id="o2", review_id="r2", object="hand_material"),
        ]
        content = json.dumps({"issues": [{"category": "visual_graphics", "title": "手部模型显示异常",
                                           "canonical_description": "角色手部模型或材质显示异常",
                                           "observation_ids": ["o2", "o1"]}]}, ensure_ascii=False)
        first = f.validate_issue_groups(content, observations)
        second = f.validate_issue_groups(content, list(reversed(observations)))
        self.assertEqual(first, second)
        self.assertEqual(first[0]["observation_ids"], ["o1", "o2"])
        self.assertEqual(first[0]["review_ids"], ["r1", "r2"])
        self.assertNotIn("severity", first[0])

    def test_16_issue_grouping_rejects_cross_category_merge(self):
        observations = [
            dict(obs("按键无效", symptom="input_has_no_effect", category="controls_input"),
                 observation_id="o1", review_id="r1"),
            dict(obs("手指异常", symptom="rendering_abnormal", category="visual_graphics"),
                 observation_id="o2", review_id="r2"),
        ]
        content = json.dumps({"issues": [{"category": "controls_input", "title": "错误合并",
                                           "canonical_description": "两个不同问题被错误合并",
                                           "observation_ids": ["o1", "o2"]}]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "Cross-category"):
            f.validate_issue_groups(content, observations)

    def test_17_issue_grouping_rejects_missing_or_duplicate_observations(self):
        observations = [dict(obs("崩溃"), observation_id="o1", review_id="r1")]
        missing = json.dumps({"issues": []})
        duplicate = json.dumps({"issues": [
            {"category": "stability", "title": "游戏崩溃", "canonical_description": "游戏发生崩溃",
             "observation_ids": ["o1"]},
            {"category": "stability", "title": "重复崩溃", "canonical_description": "重复归入同一观察",
             "observation_ids": ["o1"]},
        ]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "omitted"):
            f.validate_issue_groups(missing, observations)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            f.validate_issue_groups(duplicate, observations)

    def test_18_issue_grouping_is_one_validated_model_request(self):
        observations = [dict(obs("运行时崩溃"), observation_id="o1", review_id="r1"),
                        dict(obs("游玩中闪退"), observation_id="o2", review_id="r2")]

        def model(prompt, payload, validator, audit, attempts, model=None):
            self.assertEqual(attempts, 4)
            self.assertIsNone(model)
            self.assertTrue(all("severity" not in row for row in payload))
            return validator(json.dumps({"issues": [{"category": "stability", "title": "游戏崩溃",
                                                      "canonical_description": "游戏运行时发生崩溃",
                                                      "observation_ids": [row["observation_id"] for row in payload]}]}, ensure_ascii=False))

        with patch.object(p, "deepseek", side_effect=model) as mocked:
            issues = f.group_observations(observations)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(issues[0]["review_ids"], ["r1", "r2"])
        self.assertEqual(issues[0]["review_count"], 2)
        self.assertEqual(issues[0]["observation_count"], 2)
        self.assertEqual(issues[0]["evidence"][0]["observation_id"], "o1")

    def test_19_issue_quality_diagnostics_checks_fragmentation_and_unique_reviews(self):
        observations = [
            dict(obs("教程缺失", category="ui_ux"), observation_id="o1", review_id="r1"),
            dict(obs("目标不清", category="ui_ux"), observation_id="o2", review_id="r1"),
            dict(obs("帮助缺失", category="ui_ux"), observation_id="o3", review_id="r2"),
            dict(obs("界面难懂", category="ui_ux"), observation_id="o4", review_id="r3"),
        ]
        issues = [{"issue_id": f"i{i}", "category": "ui_ux", "title": row["description"],
                   "canonical_description": row["description"], "observation_ids": [row["observation_id"]],
                   "review_ids": [row["review_id"]]} for i, row in enumerate(observations)]
        quality = f.issue_quality_diagnostics(observations, issues)
        self.assertEqual(quality["single_observation_issue_share"], 1)
        self.assertTrue(quality["categories"][0]["fragmentation_warning"])
        issues[1]["review_ids"] = ["r1", "r1"]
        with self.assertRaisesRegex(ValueError, "unique"):
            f.issue_quality_diagnostics(observations, issues)

    def test_20_boundary_audit_only_splits_incompatible_small_groups(self):
        observations = [
            dict(obs("crashed", category="stability"), observation_id="o1", review_id="r1"),
            dict(obs("would not launch", symptom="launch_failure", category="stability"),
                 observation_id="o2", review_id="r2", description="游戏无法启动"),
            dict(obs("no tutorial", symptom="missing_tutorial", category="ui_ux"),
                 observation_id="o3", review_id="r3", object="tutorial", description="缺少教程"),
            dict(obs("no help", symptom="missing_help", category="ui_ux"),
                 observation_id="o4", review_id="r4", object="help", description="缺少帮助文档"),
        ]
        grouped = f.validate_issue_groups(json.dumps({"issues": [
            {"category": "stability", "title": "运行失败", "canonical_description": "崩溃或无法启动",
             "observation_ids": ["o1", "o2"]},
            {"category": "ui_ux", "title": "引导不足", "canonical_description": "缺少教程和帮助",
             "observation_ids": ["o3", "o4"]},
        ]}, ensure_ascii=False), observations)

        def model(prompt, payload, validator, audit, attempts, model=None):
            rows = []
            for pair in payload["pairs"]:
                same = pair["left"]["category"] == "ui_ux"
                rows.append({"pair_id": pair["pair_id"], "same_issue": same,
                             "title": "新手引导与帮助不足" if same else "",
                             "canonical_description": "缺少教程和帮助文档" if same else ""})
            return validator(json.dumps({"decisions": rows}, ensure_ascii=False))

        with patch.object(f, "ISSUE_GROUP_BATCH_SIZE", 1), patch.object(p, "deepseek", side_effect=model) as calls:
            audited = f.audit_issue_boundaries(observations, grouped)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(len(audited), 3)
        self.assertIn({"o3", "o4"}, [set(issue["observation_ids"]) for issue in audited])
        self.assertIn({"o1"}, [set(issue["observation_ids"]) for issue in audited])
        self.assertIn({"o2"}, [set(issue["observation_ids"]) for issue in audited])

    def test_21_large_issue_membership_audit_regroups_outliers(self):
        observations = [{"observation_id": f"o{i}", "review_id": f"r{i}",
                         "category": "controls_input", "object": "choices",
                         "symptom": "input_has_no_effect", "description": "操作不影响剧情",
                         "evidence": f"quote {i}"} for i in range(1, 6)]
        observations.append({"observation_id": "o6", "review_id": "r6", "category": "controls_input",
                             "object": "button", "symptom": "input_unresponsive",
                             "description": "按键输入无响应", "evidence": "button did not respond"})
        grouped = f.validate_issue_groups(json.dumps({"issues": [{"category": "controls_input",
            "title": "操作不影响剧情", "canonical_description": "玩家操作不改变剧情或结果",
            "observation_ids": [row["observation_id"] for row in observations]}]}, ensure_ascii=False), observations)

        def model(prompt, payload, validator, audit, attempts, model=None):
            rows = [{"observation_id": row["observation_id"],
                     "belongs": row["symptom"] == "input_has_no_effect", "reason": "边界检查"}
                    for row in payload["observations"]]
            return validator(json.dumps({"memberships": rows}, ensure_ascii=False))

        with patch.object(p, "deepseek", side_effect=model):
            audited = f.audit_large_issue_membership(observations, grouped)
        self.assertEqual(len(audited), 2)
        self.assertIn({"o1", "o2", "o3", "o4", "o5"},
                      [set(issue["observation_ids"]) for issue in audited])
        self.assertIn({"o6"}, [set(issue["observation_ids"]) for issue in audited])


if __name__ == "__main__":
    unittest.main(verbosity=2)
