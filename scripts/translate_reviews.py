"""Backfill cached review and evidence translations, without rerunning analysis."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import review_translation as translation
import tracking


def translate_game(game, progress=print):
    state = tracking.refresh(app.ROOT) if game == "dinner-date" else app.product_state(game)
    # Existing demos have analysis already: make their visible evidence ready first.
    analyzed = [{"text": version["text"], "language": version["language"]}
                for snap in state["snapshots"] for version in snap["reviews"].values()
                if tracking._cache_key(version) in state["cache"]]
    stages = (("analyzed_reviews", analyzed),
              ("excerpts", tracking._translation_items(state, excerpts=True)),
              ("reviews", tracking._translation_items(state)))
    results = {}
    for name, items in stages:
        progress(f"{game} / {name}")
        result = translation.translate(app.ROOT, items, progress=progress)
        results[name] = result
        if result["failed"]:
            raise ValueError(f"{game}: {result['failed']} 条译文未完成，已有结果已保存；重新运行即可补齐")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", choices=["all", *app.GAMES], default="all")
    args = parser.parse_args()
    app.load_env()
    games = app.GAMES if args.game == "all" else [args.game]
    result = {game: translate_game(game, progress=lambda message: print(message, flush=True)) for game in games}
    print(json.dumps(result, ensure_ascii=False), flush=True)
