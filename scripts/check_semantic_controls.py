"""Score explicit synthetic development checks, never human-label accuracy.

python scripts/check_semantic_controls.py BENCHMARK_JSON OUTPUT_JSON
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def check(cases, predictions):
    results = []
    for case in cases:
        actual = predictions.get(case["id"])
        reasons = []
        if actual is None:
            reasons.append("missing_prediction")
        else:
            if len(actual) != case["expected_count"]:
                reasons.append("issue_count")
            if Counter(i["aspect"] for i in actual) != Counter(case["expected_aspects"]):
                reasons.append("aspect_multiset")
            if case.get("required_severity") and any(i["severity"] != case["required_severity"] for i in actual):
                reasons.append("severity")
            if any(term.lower() in i["title"].lower() for term in case.get("forbidden_title_terms", []) for i in actual):
                reasons.append("unsupported_title_specificity")
        results.append({"id": case["id"], "passed": not reasons, "reasons": reasons, "predicted": actual})
    return {"passed": sum(r["passed"] for r in results), "total": len(cases), "cases": results,
            "limits": "Codex 编写的合成开发用例；只检查规定的数量、方面、严重程度与禁用标题词，不等于真实评论语义准确率。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark")
    parser.add_argument("output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "evaluation/semantic-controls-v1.json").read_text(encoding="utf-8"))["cases"]
    run = json.loads(Path(args.benchmark).read_text(encoding="utf-8"))
    result = {"source": args.benchmark, "prompt_hash": run["prompt_hash"], "runs": [
        dict(check(cases, r["extractions"]), round=r["round"]) for r in run["runs"]]}
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print([(r["round"], r["passed"], r["total"]) for r in result["runs"]])
