"""Offline check: cache model, request model and retry model must agree."""
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_flow_v2 as flow
import pipeline


def check():
    requests, calls = [], []
    plan = {"review_versions": {
        str(i): {"review_id": str(i), "text": f"Enjoyable game {i}"} for i in range(11)
    }}

    def response(request, timeout):
        body = json.loads(request.data)
        rows = json.loads(body["messages"][1]["content"])
        requests.append((body["model"], [row["id"] for row in rows]))
        if len(requests) == 1:
            raise HTTPError(request.full_url, 503, "Retry fixture", {}, None)
        # Keep valid rows and retry only the missing row after the HTTP retry.
        if len(requests) == 2:
            rows = rows[:-1]
        content = json.dumps({"reviews": [
            {"id": row["id"], "categories": [{"category": "none", "confidence": 1}],
             "observations": []} for row in rows
        ]})
        return io.StringIO(json.dumps({"model": body["model"], "choices": [
            {"finish_reason": "stop", "message": {"content": content}}
        ]}))

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only",
                                 "DEEPSEEK_MODEL": "deepseek-v4-pro"}), \
            patch.object(pipeline, "urlopen", side_effect=response), \
            patch.object(pipeline.time, "sleep"):
        outputs, failures, cache = flow.extract_versions(
            plan, set(plan["review_versions"]), "deepseek-v4-flash", audit=calls.append)
        assert not failures and len(outputs) == len(cache) == 11
        assert [len(ids) for _, ids in requests] == [10, 10, 1, 1]
        assert requests[2][1] == requests[1][1][-1:]
        assert {model for model, _ in requests} == {"deepseek-v4-flash"}
        assert {call["model"] for call in calls} == {"deepseek-v4-flash"}
        assert calls[0]["status"] == "failed" and calls[2]["retry_review_ids"] == requests[2][1]
        with patch.object(pipeline, "urlopen", side_effect=AssertionError("cache made a request")):
            reused, failed, _ = flow.extract_versions(
                plan, set(plan["review_versions"]), "deepseek-v4-flash", cache)
        assert reused == outputs and not failed

    def empty_response(request, timeout):
        body = json.loads(request.data)
        requests.append(body["model"])
        return io.StringIO(json.dumps({"choices": [
            {"finish_reason": "stop", "message": {"content": "{}"}}
        ]}))

    for environment, expected in (({"DEEPSEEK_MODEL": "deepseek-v4-pro"}, "deepseek-v4-pro"),
                                  ({}, "deepseek-v4-flash")):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only", **environment}, clear=True), \
                patch.object(pipeline, "urlopen", side_effect=empty_response):
            assert pipeline.deepseek("json", [], json.loads, calls.append) == {}
            assert requests[-1] == calls[-1]["model"] == expected

    # Legacy callers and test doubles need no new keyword when no model is supplied.
    def legacy_request(prompt, payload, validator, audit, attempts=2):
        content = json.dumps({"reviews": [{"id": row["id"], "issues": []} for row in payload]})
        output = validator(content)
        audit({"status": "passed"})
        return output

    with patch.object(pipeline, "deepseek", legacy_request):
        assert pipeline.extract_batch("json", [{"id": "r", "text": "Enjoyable game"}],
                                      calls.append) == ({"r": []}, {})
    print("PASS: explicit model, batch/row/HTTP retries, cache reuse, legacy/environment defaults")


if __name__ == "__main__":
    check()
