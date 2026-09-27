"""Small checks for resumable Dinner Date full-run files."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_dinner_date_full_test import _save
from pipeline import align_evidence


class FullRunChecks(unittest.TestCase):
    def test_evidence_alignment_restores_steam_bbcode(self):
        source = "графическое исполнение проекта [b]Stout Games[/b] оставляет желать лучшего"
        quote = "графическое исполнение проекта Stout Games оставляет желать лучшего"
        self.assertEqual(align_evidence(quote, source), source)

    def test_checkpoint_write_is_atomic_and_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoint.json"
            _save(path, {"completed": ["a", "b"]})
            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "completed": [\n    "a",\n    "b"\n  ]\n}')
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
