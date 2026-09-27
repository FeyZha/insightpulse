"""Export all `none` reviews from the latest Dinner Date audit run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "evaluation" / "dinner-date-observation-audit"
OUTPUT_DIR = AUDIT_DIR / "none-review-audit"


def main() -> None:
    runs = sorted(AUDIT_DIR.glob("run-*.json"), key=lambda path: path.stat().st_mtime)
    if not runs:
        raise FileNotFoundError(f"No audit JSON found in {AUDIT_DIR}")

    source_path = runs[-1]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    reviews = source.get("reviews", [])
    none_reviews = [
        review
        for review in reviews
        if [item.get("category") for item in review.get("categories", [])] == ["none"]
        and not review.get("observations")
    ]

    expected = source.get("summary", {}).get("none_reviews")
    if expected is not None:
        assert len(none_reviews) == expected, (len(none_reviews), expected)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exported = {
        "source_file": str(source_path.relative_to(ROOT)),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selection_rule": "categories == [{category: none}] and observations == []",
        "count": len(none_reviews),
        "reviews": none_reviews,
    }
    (OUTPUT_DIR / "none-reviews.json").write_text(
        json.dumps(exported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# Dinner Date `none` Review 排查清单",
        "",
        f"来源：`{source_path.name}`",
        f"共 {len(none_reviews)} 条。筛选条件：仅有 `none` Category 且 Observation 为空。",
        "",
    ]
    for index, review in enumerate(none_reviews, start=1):
        category = review["categories"][0]
        lines.extend(
            [
                f"## {index}. Review {review['review_id']}",
                "",
                f"- recommended: `{str(review.get('recommended')).lower()}`",
                f"- language: `{review.get('language') or ''}`",
                f"- category: `none` (confidence: `{category.get('confidence')}`)",
                "- observations: `[]`",
                "",
                "**原始 Review text**",
                "",
                "\n".join("> " + line for line in (review.get("text") or "").splitlines())
                or "> ",
                "",
            ]
        )
    (OUTPUT_DIR / "none-reviews.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Exported {len(none_reviews)} none reviews from {source_path.name}")
    print(OUTPUT_DIR / "none-reviews.md")
    print(OUTPUT_DIR / "none-reviews.json")


if __name__ == "__main__":
    main()
