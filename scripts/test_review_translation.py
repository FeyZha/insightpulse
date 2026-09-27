"""Offline translation cache, completeness, source-language and retry checks."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review_translation as translation


def fake_model(prompt, payload, validator, audit, attempts=2, model=None):
    assert prompt == translation.PROMPT and model == translation.MODEL
    result = validator(json.dumps({"translations": [
        {"id": row["id"], "text_zh": "完整中文译文：" + row["text"]} for row in payload["items"]
    ]}, ensure_ascii=False))
    audit({"status": "passed", "model": model})
    return result


class TranslationChecks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        credentials = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"})
        credentials.start()
        self.addCleanup(credentials.stop)

    def test_lookup_is_readonly_and_source_detection_does_not_trust_false_chinese_tag(self):
        with patch.object(translation.pipeline, "deepseek", side_effect=AssertionError("lookup called model")):
            for text, language in (("中文评论，运行很流畅", "schinese"), ("", "english"),
                                   ("  123.45 ！！？", "unknown"), ("FPS 下降严重", "zh-cn")):
                result = translation.lookup(self.root, text, language)
                self.assertEqual((result["status"], result["text_zh"]), ("source", text))
            for text, language in (("This game crashes", "schinese"), ("中文 This game crashes frequently", "schinese"),
                                   ("這款遊戲很好", "tchinese"), ("The game is fine", "unknown")):
                self.assertEqual(translation.lookup(self.root, text, language)["status"], "pending")
        self.assertEqual(translation.revision(self.root), 0)
        self.assertFalse((self.root / "runtime").exists())
        # A concurrent first translation may have created the file before committing its table.
        path = translation._path(self.root)
        path.parent.mkdir()
        path.touch()
        revision = translation.revision(self.root)
        self.assertEqual(translation.lookup(self.root, "English review", "english")["status"], "pending")
        self.assertEqual(translation.revision(self.root), revision)

    def test_deduplication_context_isolation_and_cached_retry_need_no_key(self):
        item = dict(text="It is broken", language="english", context="The controller is broken.")
        other = dict(item, context="The save file is broken.")
        with patch.object(translation.pipeline, "deepseek", side_effect=fake_model):
            result = translation.translate(self.root, [item, item, other])
        self.assertEqual(result, dict(total=2, ready=2, failed=0, calls=1))
        revision = translation.revision(self.root)
        self.assertGreater(revision, 0)
        self.assertEqual(translation.lookup(self.root, **item)["status"], "ready")
        self.assertEqual(translation.lookup(self.root, **other)["status"], "ready")
        with patch.object(translation.sqlite3, "connect", side_effect=AssertionError("Repeated lookup reopened the database")):
            self.assertEqual(translation.lookup(self.root, **item)["status"], "ready")
        self.assertEqual(translation.lookup(self.root, item["text"], "english")["status"], "pending")
        self.assertEqual(translation.lookup(self.root, "It is fixed", "english", item["context"])["status"], "pending")
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(translation.pipeline, "deepseek", side_effect=AssertionError("cache made a request")):
            self.assertEqual(translation.translate(self.root, [item, other]), dict(total=2, ready=2, failed=0, calls=0))
        self.assertEqual(translation.revision(self.root), revision)
        with patch.object(translation, "VERSION", "new-rules"):
            self.assertEqual(translation.lookup(self.root, **item)["status"], "pending")
        with patch.object(translation, "PROMPT", translation.PROMPT + "\nNew rules"):
            self.assertEqual(translation.lookup(self.root, **item)["status"], "pending")

    def test_response_requires_exact_ids_and_nonempty_strings(self):
        rows = [{"id": "a"}, {"id": "b"}]
        valid = [{"id": "a", "text_zh": "第一条完整译文"}, {"id": "b", "text_zh": "第二条完整译文"}]
        self.assertEqual(len(translation._validate(json.dumps({"translations": valid}), rows)), 2)
        for invalid in (valid[:1], valid + [valid[0]], [valid[0], valid[0]],
                        [valid[0], {"id": "unknown", "text_zh": "额外内容"}],
                        [valid[0], {"id": "b", "text": "原文不能冒充译文"}],
                        [valid[0], dict(valid[1], text="只允许输出译文字段")],
                        [valid[0], {"id": "b", "text_zh": "  "}],
                        [valid[0], {"id": "b", "text_zh": 123}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                translation._validate(json.dumps({"translations": invalid}), rows)

    def test_truncated_response_is_failed_then_recovers_without_erasing_success(self):
        good, pending = dict(text="A good game", language="english"), dict(text="Still crashes", language="english")
        with patch.object(translation.pipeline, "deepseek", side_effect=fake_model):
            translation.translate(self.root, [good])
        ready = translation.lookup(self.root, **good)
        revision = translation.revision(self.root)

        def truncated(*args, **kwargs):
            return io.StringIO(json.dumps({"choices": [
                {"finish_reason": "length", "message": {"content": '{"translations":['}}
            ]}))

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), \
                patch.object(translation.pipeline, "urlopen", side_effect=truncated), \
                patch.object(translation.pipeline.time, "sleep"):
            failed = translation.translate(self.root, [good, pending])
        self.assertEqual(failed, dict(total=2, ready=1, failed=1, calls=2))
        self.assertEqual(translation.lookup(self.root, **pending)["status"], "failed")
        self.assertIsNone(translation.lookup(self.root, **pending)["text_zh"])
        self.assertEqual(translation.lookup(self.root, **good), ready)
        self.assertNotEqual(translation.revision(self.root), revision)
        with patch.object(translation.pipeline, "deepseek", side_effect=fake_model):
            recovered = translation.translate(self.root, [good, pending])
        self.assertEqual(recovered, dict(total=2, ready=2, failed=0, calls=1))
        self.assertEqual(translation.lookup(self.root, **pending)["status"], "ready")

    def test_long_review_is_fully_partitioned_with_context_and_saved_after_every_part(self):
        text = "The game does not crash, but it is slow.\n" * 220
        seen = []

        def model(prompt, payload, validator, audit, attempts=2, model=None):
            seen.extend(payload["items"])
            self.assertEqual([row["id"] for row in payload["items"]],
                             [f"r{i + 1}" for i in range(len(payload["items"]))])
            self.assertLessEqual(sum(len(row["text"]) for row in payload["items"]), 3500)
            self.assertTrue(all(row["context"] == text for row in payload["items"]))
            return fake_model(prompt, payload, validator, audit, attempts, model)

        with patch.object(translation.pipeline, "deepseek", side_effect=model):
            result = translation.translate(self.root, [dict(text=text, language="english")])
        self.assertEqual((result["total"], result["ready"], result["failed"]), (1, 1, 0))
        self.assertEqual(sorted(row["text"] for row in seen), sorted(translation._parts(text)))
        self.assertEqual(sum(len(row["text"]) for row in seen), len(text))
        self.assertEqual(translation.lookup(self.root, text, "english")["status"], "ready")

    def test_source_only_batch_does_not_need_credentials_or_database(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(translation.pipeline, "deepseek", side_effect=AssertionError("source called model")):
            self.assertEqual(translation.translate(self.root, [dict(text="已经是简体中文", language="schinese")]),
                             dict(total=1, ready=1, failed=0, calls=0))
        self.assertEqual(translation.revision(self.root), 0)

    def test_missing_key_fails_before_creating_cache_or_submitting_work(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(translation.pipeline, "deepseek", side_effect=AssertionError("missing key made a request")):
            with self.assertRaisesRegex(ValueError, "API Key"):
                translation.translate(self.root, [dict(text="English review", language="english")])
        self.assertEqual(translation.revision(self.root), 0)

    def test_account_errors_stop_after_current_group_and_keep_successful_results(self):
        items = [dict(text=("Good " if i == 0 else f"Blocked {i} ") + "review " * 260, language="english")
                 for i in range(12)]
        for code in (401, 402, 403):
            with self.subTest(code=code):
                root, requests = self.root / str(code), []

                def response(request, timeout):
                    rows = json.loads(json.loads(request.data)["messages"][1]["content"])["items"]
                    requests.append(rows)
                    if not rows[0]["text"].startswith("Good "):
                        error = HTTPError(request.full_url, code, "Account error", {}, io.BytesIO())
                        error.close()
                        raise error
                    content = json.dumps({"translations": [{"id": row["id"], "text_zh": "完整中文译文"} for row in rows]})
                    return io.StringIO(json.dumps({"choices": [
                        {"finish_reason": "stop", "message": {"content": content}}
                    ]}))

                with patch.object(translation.pipeline, "urlopen", side_effect=response):
                    result = translation.translate(root, items)
                self.assertEqual(result, dict(total=12, ready=1, failed=11, calls=3))
                self.assertEqual(len(requests), 3)
                self.assertEqual(translation.lookup(root, **items[0])["status"], "ready")
                self.assertEqual(translation.lookup(root, **items[-1])["status"], "failed")
                self.assertIn("停止后续翻译", translation.lookup(root, **items[-1])["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
