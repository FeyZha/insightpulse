"""Verify real-data demo correction, persistence and export over HTTP; no paid calls."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app

FIXTURE = ROOT / "examples/reviewed-run.json"
if not FIXTURE.exists():
    print("SKIP: archived reviewed-run fixture is not present; product API coverage lives in test_product_api.py")
    raise SystemExit(0)


@contextmanager
def serve():
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    app.PORT = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{app.PORT}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(base, path, body=None):
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json", "X-InsightPulse-Token": app.TOKEN, "Origin": base})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


with tempfile.TemporaryDirectory() as folder:
    app.RUNTIME = Path(folder)
    app.DB = app.RUNTIME / "demo.sqlite3"
    app.init_db()
    original = json.loads(FIXTURE.read_text(encoding="utf-8"))
    app.save(original)
    rid = original["id"]
    with serve() as base:
        # Verify the start of the product path against the packaged real snapshot.
        snapshots = request(base, "/api/state")["datasets"]
        snapshot = next(s for s in snapshots if s["name"] == original["dataset"])
        assert snapshot["announcements"] and snapshot["count"] > 0
        create = dict(dataset=original["dataset"], event=original["plan"]["event"],
                      days=7, limit=12, title="自动化事件确认检查", event_confirmed=False)
        try:
            request(base, "/api/create", create)
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("Unconfirmed news was accepted as an update")
        draft = request(base, "/api/create", dict(create, event_confirmed=True))
        assert draft["status"] == "draft" and draft["batch_size"] == 10
        assert draft["plan"]["start"] == create["event"] - 7 * 86400
        assert draft["plan"]["end"] == create["event"] + 7 * 86400
        assert len(draft["plan"]["selected"]) == 12 and draft["metrics"]["pending"] == 12
        assert draft["metrics"]["totals"] == {"before": 0, "after": 0}
        try:
            request(base, "/api/export", dict(id=draft["id"]))
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("Unreviewed draft was exported as a confirmed report")
        loaded = request(base, "/api/run?id=" + rid)
        selected = {r["id"]: r for r in loaded["plan"]["selected"]}
        successful = set(loaded["extractions"])
        totals = {period: sum(r["period"] == period and key in successful for key, r in selected.items())
                  for period in ("before", "after")}
        assert totals == loaded["metrics"]["totals"]
        for group in loaded["metrics"]["issues"]:
            for period in totals:
                ids = {i["review_id"] for i in group["evidence"] if selected[i["review_id"]]["period"] == period}
                assert len(ids) == group["counts"][period]
                assert group["shares"][period] == (len(ids) / totals[period] if totals[period] else None)
            assert all(i["evidence"] in selected[i["review_id"]]["text"] for i in group["evidence"])
        opinion = loaded["opinions"][0]
        issue = {k: opinion[k] for k in ("title", "aspect", "evidence", "severity")}
        issue["title"] = "演示纠错检查：" + issue["title"]
        corrected = request(base, "/api/manual", dict(id=rid, review_id=opinion["review_id"], issues=[issue]))
        assert corrected["model_extractions"] == original["model_extractions"]
        group = next(g for g in corrected["groups"] if g["title"] == issue["title"])
        note = "Codex 自动化功能检查；不是独立人工评测。"
        request(base, "/api/decision", dict(id=rid, group_id=group["id"], status="confirmed", note=note))
        exported = request(base, "/api/export", dict(id=rid))
        markdown = Path(exported["path"]).read_bytes().decode("utf-8")
        assert issue["title"] in markdown and note in markdown and issue["evidence"] in markdown
        assert selected[opinion["review_id"]]["url"] in markdown
        assert request(base, "/api/report?id=" + rid)["markdown"] == markdown
    app.init_db()
    with serve() as base:
        assert request(base, "/api/run?id=" + draft["id"])["status"] == "draft"
        reopened = request(base, "/api/run?id=" + rid)
        assert reopened["decisions"][group["id"]] == {"status": "confirmed", "note": note}
        assert reopened["extractions"][opinion["review_id"]] == [issue]
        assert request(base, "/api/report?id=" + rid)["markdown"] == markdown
print("PASS: HTTP event confirmation, windows, pending denominator, export gate, real-data recount, correction, confirmation, export, server reopen; no AI calls")
