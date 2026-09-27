"""Apply submitted human findings to a separate delivery copy; preserve AI originals."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import pipeline as p
from scripts.score_final_acceptance import score


if __name__ == "__main__":
    read = lambda name: json.loads((ROOT / "evaluation" / name).read_text(encoding="utf-8"))
    source = read("final-review-source.json")
    labels = score((ROOT / "docs/FINAL_HUMAN_ACCEPTANCE.md").read_text(encoding="utf-8"), read("final-review-manifest.json"), source)
    assert labels["support"]["sample_gate_passed"]
    run = copy.deepcopy(source)
    run.update(id="final-review-0e702576a84f9358", title="7.0.2 · 独立验收后核查复盘", decisions={},
               demo_provenance="Historical real AI output, with submitted independent human findings applied to a separate delivery copy.",
               acceptance_notes=[
                   "独立抽检问题支持 19/20、归并 11/12，均为修正前成绩；不能代表全部评论准确率。",
                   "已移除‘难度曲线不合理’这一缺乏依据的推断，拆开付费模式与游戏售价两类问题。",
                   "评论 233721410 被指出遗漏持续 bug 与修复不及时；其相关问题暂不确认、不进入本简报，原文和模型输出保留供补充核查。",
                   "无遗漏检查覆盖 10 条，其中 1 条不确定；未建立完整问题级召回率。",
                   "仅确认所有有效成员均获得本次人工支持、且没有未决遗漏的组；其余组保持待核查。"],
               message="独立验收已应用；简报只包含已支持且无未决遗漏的确认组。原始模型输出保持不变。")
    supported = {x["id"] for x in labels["issues"] if x["支持判断"] == "支持"}
    rejected = {x["id"] for x in labels["issues"] if x["支持判断"] == "不支持"}
    unresolved = {x["id"] for x in labels["omissions"] if x["status"] != "none"}
    assert rejected == {"234022006:0"}
    run["removed_opinions"] = sorted(set(run.get("removed_opinions", [])) | rejected)
    run["edits"].append(dict(kind="independent_acceptance", removed=sorted(rejected), source="docs/FINAL_HUMAN_ACCEPTANCE.md"))
    # Human identified this one classification as wrong; do not reinterpret other labels.
    changed = next(o for o in run["opinions"] if o["id"] == "233955031:1")
    changed["aspect"] = "Stability"
    run["extractions"][changed["review_id"]][1]["aspect"] = "Stability"
    for group in run["groups"]:
        group["opinion_ids"] = [oid for oid in group["opinion_ids"] if oid != changed["id"]]
    run["groups"] = [g for g in run["groups"] if g["opinion_ids"]]
    run["groups"].append(dict(id=p.fingerprint([changed["id"], "human-classification"])[:16],
                              title="光照族敌人存在 bug", aspect="Stability", opinion_ids=[changed["id"]]))
    mismatch = [x for x in labels["pairs"] if x["correct"] is False]
    assert len(mismatch) == 1 and mismatch[0]["a"] == "233238397:0" and mismatch[0]["b"] == "233999870:0"
    group = next(g for g in run["groups"] if mismatch[0]["a"] in g["opinion_ids"])
    assert set(group["opinion_ids"]) == {mismatch[0]["a"], mismatch[0]["b"]}
    run["groups"].remove(group)
    for oid, title in [(mismatch[0]["a"], "付费模式劝退新玩家"), (mismatch[0]["b"], "游戏售价过高")]:
        run["groups"].append(dict(id=p.fingerprint([oid, "human-split"])[:16], title=title,
                                  aspect="Monetization", opinion_ids=[oid]))
    by_id = {o["id"]: o for o in run["opinions"]}
    for group in run["groups"]:
        active = set(group["opinion_ids"]) - set(run["removed_opinions"])
        if active and active <= supported and not any(by_id[oid]["review_id"] in unresolved for oid in active):
            run["decisions"][group["id"]] = dict(status="confirmed", note="组内有效问题已获本次独立人工原文支持；仍只作为调查线索，不认定技术根因。")
    run["edits"].append(dict(kind="independent_acceptance", reclassified=changed["id"], split_pair=mismatch[0],
                             withheld_reviews=sorted(unresolved), source="docs/FINAL_HUMAN_ACCEPTANCE.md"))
    assert run["model_extractions"] == source["model_extractions"]
    metrics = p.metrics(run)
    exported = [g for g in metrics["issues"] if g["decision"]["status"] == "confirmed"]
    assert exported and all(o["id"] in supported and o["review_id"] not in unresolved for g in exported for o in g["evidence"])
    (ROOT / "examples/reviewed-run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "docs/REAL_REVIEW_BRIEF.md").write_bytes(app.report(run).encode("utf-8"))
    print(json.dumps(dict(confirmed_groups=len(exported), source_preserved=True, rejected=len(rejected),
                          withheld_review_ids=sorted(unresolved)), ensure_ascii=False))
