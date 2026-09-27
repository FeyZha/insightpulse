"""Synthetic data-only checks. No API calls, AI imports or model-effect claims."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import align_external_snapshots as f


def raw(rid="1", text="A concrete review", updated=1500):
    return dict(recommendationid=rid, review=text, timestamp_created=1000,
                timestamp_updated=updated, language="english", voted_up=True)


def snap(rows, source="github_archive"):
    return f.normalized_snapshot([(r, {"fixture": True}, None) for r in rows], source)


class AlignmentChecks(unittest.TestCase):
    def compare(self, before, after):
        return f.align(snap(before), snap(after, "steam_api"))

    def test_01_identical_text(self):
        r = self.compare([raw()], [raw()])
        self.assertEqual(r["rows"][0]["category"], "snapshot_unchanged")
        self.assertEqual(r["counts"]["snapshot_unchanged"], 1)

    def test_02_changed_text(self):
        r = self.compare([raw()], [raw(text="Different review", updated=1600)])
        self.assertEqual(r["rows"][0]["category"], "review_revision")
        self.assertTrue(r["rows"][0]["text_changed"])

    def test_03_history_only(self):
        r = self.compare([raw()], [])
        self.assertEqual(r["rows"][0]["category"], "history_only")
        self.assertIsNone(r["rows"][0]["text_changed"])

    def test_04_current_only(self):
        r = self.compare([], [raw()])
        self.assertEqual(r["rows"][0]["category"], "current_only")
        self.assertIsNone(r["overlap_rate"])

    def test_05_metadata_only(self):
        r = self.compare([raw()], [raw(updated=1600)])
        self.assertEqual(r["rows"][0]["category"], "metadata_changed")
        self.assertEqual(r["counts"]["metadata_changed"], 1)
        self.assertFalse(r["rows"][0]["text_changed"])

    def test_06_duplicates_identified_not_overwritten(self):
        same = snap([raw(), raw()])
        self.assertEqual(same["count"], 1)
        self.assertEqual(same["duplicate_review_ids"], {"1": 2})
        conflict = snap([raw(), raw(text="changed")])
        self.assertEqual(conflict["count"], 0)
        self.assertEqual(conflict["conflicting_duplicate_ids"], ["1"])

    def test_07_invalid_records_rejected(self):
        for field, value in (("review", " \n"), ("recommendationid", None), ("timestamp_created", "yesterday"),
                             ("timestamp_updated", -1), ("timestamp_updated", True), ("timestamp_updated", 999),
                             ("voted_up", "false")):
            item = raw(); item[field] = value
            with self.subTest(field=field, value=value):
                result = snap([item])
                self.assertEqual(result["count"], 0)
                self.assertEqual(len(result["rejected"]), 1)

    def test_08_raw_normalized_comparisons_separate(self):
        r = self.compare([raw(text="hello\n world")], [raw(text="hello world")])["rows"][0]
        self.assertFalse(r["raw_text_equal"])
        self.assertTrue(r["normalized_text_equal"])
        self.assertTrue(r["text_changed"])
        self.assertEqual(r["category"], "source_text_changed")
        self.assertEqual(r["history"]["text"], "hello\n world")

    def test_09_review_rows_are_lean(self):
        item = raw()
        r = snap([item])["reviews"][0]
        self.assertEqual(set(r), {"app_id", "review_id", "text", "created_at", "updated_at",
                                  "language", "recommended"})

    def test_10_schema_difference_not_value_change(self):
        after = raw(); after["new_api_field"] = False
        r = self.compare([raw()], [after])["rows"][0]
        self.assertEqual(r["category"], "snapshot_unchanged")
        self.assertEqual(r["metadata_fields_added"], ["new_api_field"])

    def test_11_display_masking_does_not_erase_raw_diff(self):
        r = self.compare([raw(text="word here")], [raw(text="♥♥♥♥ here")])["rows"][0]
        self.assertEqual(r["category"], "source_text_changed")
        self.assertTrue(r["differences_only_at_heart_characters"])
        self.assertFalse(r["normalized_text_equal"])
        r = self.compare([raw(text="word here")], [raw(text="♥♥♥♥ gone")])["rows"][0]
        self.assertFalse(r["differences_only_at_heart_characters"])

    def test_12_pagination_retry_raw_audit_and_empty_terminal(self):
        def response(rows, cursor):
            obj = io.BytesIO(json.dumps(dict(success=1, reviews=rows, cursor=cursor)).encode())
            obj.status, obj.headers = 200, {}
            return obj
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            replies = [HTTPError(f.API, 500, "test failure", {}, io.BytesIO(b"test error")),
                       response([raw()], "next"), response([], "next")]
            with patch.object(f, "urlopen", side_effect=replies) as network, patch.object(f.time, "sleep"):
                f.collect(folder)
            result = json.loads((folder / "collection.json").read_text(encoding="utf-8"))
            self.assertTrue(result["cursor_exhausted"])
            self.assertEqual((result["page_count"], result["retries"], result["request_failures"]), (2, 1, 1))
            self.assertEqual(network.call_count, 3)
            self.assertEqual(len(list((folder / "steam-pages").glob('*.raw'))), 3)

    def test_13_repeated_cursor_not_complete(self):
        response = io.BytesIO(json.dumps(dict(success=1, reviews=[raw()], cursor="*")).encode())
        response.status, response.headers = 200, {}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(f, "urlopen", return_value=response), patch.object(f.time, "sleep"):
                with self.assertRaises(ValueError):
                    f.collect(Path(directory))
            result = json.loads((Path(directory) / "collection.json").read_text(encoding="utf-8"))
            self.assertFalse(result["cursor_exhausted"])

    def test_14_export_separates_snapshot_and_review_fields(self):
        source = snap([raw()], "steam_api")
        source.update(observed_at="2026-09-11T00:00:00+00:00", observed_at_precision="instant",
                      source_url=f.API, response_hash=["abc"])
        result = f.export_snapshot(source)
        sid = result["snapshot"]["snapshot_id"]
        self.assertEqual(set(result), {"snapshot", "reviews"})
        self.assertEqual(result["reviews"][0]["snapshot_id"], sid)
        self.assertNotIn("source_url", result["reviews"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
