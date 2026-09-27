"""Cache hits, game isolation and file-driven invalidation, without real writes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class MonitoringCacheChecks(unittest.TestCase):
    def test_file_updates_additions_and_deletions_invalidate(self):
        app.monitoring_bundle.cache_clear()
        self.addCleanup(app.monitoring_bundle.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "evaluation/game"
            folder.mkdir(parents=True)
            (root / "data").mkdir()
            (root / "runtime/tracking").mkdir(parents=True)
            files = [folder / "product.json", root / "data/steam-a.json",
                     app.tracking._path(root), app.tracking.reanalysis_path(root, "dinner-date"),
                     root / "runtime/tracking/preferences.json", app.translation._path(root)]
            for path in files:
                path.write_text("initial", encoding="utf-8")
            with patch.object(app, "ROOT", root), \
                    patch.object(app, "game_config", return_value={"folder": folder}), \
                    patch.object(app, "product_state", side_effect=lambda game: {"game": game}) as read, \
                    patch.object(app.tracking_views, "tracking_state", side_effect=lambda _, state: state), \
                    patch.object(app.tracking, "monitoring", side_effect=lambda state: dict(state)):
                def query(game="dinner-date"):
                    return app.monitoring_bundle(game, app.monitoring_revision("dinner-date"))[1]
                query(); query()
                self.assertEqual(read.call_count, 1)
                self.assertEqual(query("ostranauts"), {"game": "ostranauts"})
                self.assertEqual(query(), {"game": "dinner-date"})
                self.assertEqual(read.call_count, 2)
                for path in files:
                    before = read.call_count
                    path.write_text("updated content", encoding="utf-8")
                    query(); query()
                    self.assertEqual(read.call_count, before + 1, path)
                new = root / "data/steam-new.json"
                new.write_text("new snapshot", encoding="utf-8")
                before = read.call_count
                query()
                self.assertEqual(read.call_count, before + 1)
                # Deletion may reuse the earlier, identical file set safely.
                new.unlink()
                self.assertNotIn(str(new.resolve()), str(app.monitoring_revision("dinner-date")))

    def test_cached_report_is_reused_for_evidence(self):
        from scripts.test_issue_tracking import scenario
        state = scenario(1, 2, 10, 10)
        report = app.tracking.monitoring(state)
        with patch.object(app.tracking, "monitoring", side_effect=AssertionError("recomputed chart")):
            result = app.tracking_views.monitoring_evidence(state, "a", "issue", report=report)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["snapshot_id"], "a")


if __name__ == "__main__":
    unittest.main()
