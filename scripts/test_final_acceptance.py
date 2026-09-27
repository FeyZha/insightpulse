"""Check submitted-form parsing and final-copy export boundaries without AI calls."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from scripts.score_final_acceptance import score

required = [ROOT / "evaluation/final-review-source.json", ROOT / "evaluation/final-review-manifest.json",
            ROOT / "examples/reviewed-run.json"]
if not all(path.exists() for path in required):
    print("SKIP: archived human-acceptance fixtures are not present in the current Dinner Date-only scope")
    raise SystemExit(0)

read = lambda name: json.loads((ROOT / name).read_text(encoding="utf-8"))
source = read("evaluation/final-review-source.json")
manifest = read("evaluation/final-review-manifest.json")
text = (ROOT / "docs/FINAL_HUMAN_ACCEPTANCE.md").read_text(encoding="utf-8")
result = score(text, manifest, source)
assert result["support"]["supported"] == 19 and result["support"]["assessed"] == 20
assert result["grouping"] == dict(correct=11, assessed=12, assigned=12)
assert result["timing"]["manual_minutes"] == 30 and result["timing"]["assisted_minutes"] == 20
for changed in (text.replace("支持判断：**支持**", "支持判断：", 1),
                text.replace("233213478:0", "missing-id", 1),
                text.replace("确认以上为本人独立完成：确认", "确认以上为本人独立完成：")):
    try:
        score(changed, manifest, source)
    except ValueError:
        pass
    else:
        raise AssertionError("Incomplete or altered submission was accepted")
run = read("examples/reviewed-run.json")
assert run["model_extractions"] == source["model_extractions"]
stats = app.p.metrics(run)
confirmed = [g for g in stats["issues"] if g["decision"]["status"] == "confirmed"]
assert len(confirmed) == 8
assert all(o["id"] != "234022006:0" and o["review_id"] != "233721410" for g in confirmed for o in g["evidence"])
assert "验收与待核查事项" in app.report(run)
print("PASS: independent submission counts, invalid-label rejection, immutable originals, unresolved export exclusion")
