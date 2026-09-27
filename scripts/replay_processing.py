"""Replay saved extraction responses without credentials, network or new model generation.

python scripts/replay_processing.py AUDIT_JSON OUTPUT_JSON
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as p


def replay(audit):
    strict, repaired, failures, repairs = {}, {}, {}, []
    used = []
    for offset in range(0, len(audit["input"]), 5):
        batch = audit["input"][offset:offset + 5]
        payload = [{"id": r["id"], "text": r["text"]} for r in batch]
        matches = [c for c in audit["calls"] if c["input_hash"] == p.fingerprint(payload)
                   and c["prompt_hash"] == p.fingerprint(audit["prompt"])]
        if not matches:
            raise ValueError("Missing saved response; replay requires uncached input batches")
        call = matches[0]  # First response only: no choosing the better retry.
        if call.get("finish_reason") != "stop" or not call.get("content"):
            failures.update({r["id"]: "Incomplete saved response" for r in batch})
            continue
        used.append(call["input_hash"])
        try:
            strict.update(p.validate_extraction(call["content"], batch))
        except ValueError:
            pass
        try:
            output, rejected, aligned = p.partition_extraction(call["content"], batch)
            repaired.update(output)
            failures.update(rejected)
            repairs.extend(aligned)
        except ValueError as exc:
            failures.update({r["id"]: str(exc) for r in batch})
    assert len(repaired) + len(failures) == len(audit["input"])
    texts = {r["id"]: r["text"] for r in audit["input"]}
    assert all(i["evidence"] in texts[rid] for rid, issues in repaired.items() for i in issues)
    return {"kind": "offline_saved_response_replay", "source_run": audit["run_id"],
            "processing_version": p.PROCESSING_VERSION, "new_api_requests": 0,
            "selected": len(audit["input"]), "strict_batch_passed": len(strict),
            "repaired_passed": len(repaired), "failed": failures, "evidence_repairs": repairs,
            "recovered_review_ids": sorted(set(repaired) - set(strict)),
            "response_input_hashes": used, "validated_extractions": repaired,
            "limits": "回放首次原始响应；不是新模型实测或准确率评测。未重跑归并，不修正语义错误，不写入产品历史结果。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit")
    parser.add_argument("output")
    args = parser.parse_args()
    result = replay(json.loads(Path(args.audit).read_text(encoding="utf-8")))
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ("selected", "strict_batch_passed", "repaired_passed", "new_api_requests")}))
