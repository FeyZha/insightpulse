"""Build and evaluate cached Evidence Capsules for the fixed Dinner Date snapshot."""
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import evidence_capsules as capsules

RUN = ROOT / "evaluation/dinner-date-full-run/run-20260924T091235243769Z.json"
EMBEDDINGS = ROOT / "evaluation/dinner-date-full-run/embeddings-minilm-v2.3-830.json"
CACHE = ROOT / "runtime/evidence_capsules/dinner-date"
REPORT = ROOT / "evaluation/dinner-date-full-run/evidence-capsules-v1.2.json"


def main():
    snapshot = json.loads(RUN.read_text(encoding="utf-8"))
    embedding_data = json.loads(EMBEDDINGS.read_text(encoding="utf-8"))
    embedding_meta = {key: embedding_data[key] for key in ("model", "dimensions", "input_format")}
    observations = {row["observation_id"]: row for row in snapshot["observations"]}
    issue_group_version = snapshot["issue_group"]["version"]
    results, cache_hits = [], 0
    for issue in snapshot["issues"]:
        rows = [observations[oid] for oid in issue["observation_ids"]]
        capsule, hit = capsules.cached_capsule(CACHE, issue, rows, embedding_data["vectors"],
                                                issue_group_version, embedding_meta)
        cache_hits += hit
        results.append({
            "issue_id": issue["issue_id"],
            "category": issue["category"],
            "title": issue["title"],
            "review_count": capsule["review_count"],
            "observation_count": capsule["observation_count"],
            "manifestation_count": len(capsule["manifestations"]),
            "representative_count": sum(len(item["representatives"]) for item in capsule["manifestations"]),
            "manifestation_sizes": [item["observation_count"] for item in capsule["manifestations"]],
            "labels": [item["label"] for item in capsule["manifestations"]],
            "evidence_quality": capsule["evidence_quality"],
        })
    observation_count = sum(row["observation_count"] for row in results)
    manifestation_count = sum(row["manifestation_count"] for row in results)
    representative_count = sum(row["representative_count"] for row in results)
    singleton_manifestations = sum(row["evidence_quality"]["unclustered_observations"]
                                    for row in results)
    source_singleton_issues = sum(row["observation_count"] == 1 for row in results)
    membership_rows = sorted([[issue["issue_id"], sorted(sorted(item["observation_ids"])
                       for item in json.loads((CACHE / f"{issue['issue_id']}.json").read_text(
                           encoding="utf-8"))["manifestations"])] for issue in snapshot["issues"]])
    report = {
        "dataset": "dinner-date",
        "source": str(RUN.relative_to(ROOT)),
        "issue_group_version": issue_group_version,
        "capsule_version": capsules.CAPSULE_VERSION,
        "algorithm_version": capsules.ALGORITHM_VERSION,
        "label_version": capsules.LABEL_VERSION,
        "similarity_threshold": capsules.DEFAULT_SIMILARITY_THRESHOLD,
        "embedding": embedding_meta,
        "cost": {"embedding_calls": 0, "llm_calls": 0},
        "cache": {"hits": cache_hits, "misses": len(results) - cache_hits},
        "summary": {
            "issues": len(results),
            "observations": observation_count,
            "manifestations": manifestation_count,
            "representatives": representative_count,
            "coverage": round(sum(row["observation_count"] * row["evidence_quality"]["coverage"]
                                  for row in results) / observation_count, 4),
            "representative_to_observation_ratio": round(representative_count / observation_count, 4),
            "evidence_reduction_share": round(1 - representative_count / observation_count, 4),
            "single_observation_manifestations": singleton_manifestations,
            "source_singleton_issues": source_singleton_issues,
            "internal_single_observation_manifestations": singleton_manifestations - source_singleton_issues,
            "minority_manifestations": sum(row["evidence_quality"]["minority_manifestations"]
                                             for row in results),
            "contradictory_reviews": sum(row["evidence_quality"]["contradictory_review_count"]
                                          for row in results),
            "membership_hash": hashlib.sha256(json.dumps(membership_rows, sort_keys=True,
                                             separators=(",", ":")).encode()).hexdigest(),
            "max_label_characters": max(len(label) for row in results for label in row["labels"]),
        },
        "issues": results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"] | report["cache"] | report["cost"], ensure_ascii=False, indent=2))
    print(REPORT)


if __name__ == "__main__":
    main()
