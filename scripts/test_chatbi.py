"""Check the ChatBI data contract and HTTP surface without paid model calls."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


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


def get(base, path):
    with urlopen(base + path, timeout=10) as response:
        return json.load(response)


snapshot = app.chatbi_snapshot()
assert snapshot["metrics"] == {"reviews": 830, "observations": 377, "issues": 56,
                               "none_reviews": 583, "issue_reviews": 247, "coverage": 0.2976}
assert snapshot["issues"][0]["title"] == "玩家操作不影响游戏进程与结局"
assert snapshot["issues"][0]["reviews"] == 129 and snapshot["issues"][0]["evidence"]

valid = json.dumps({
    "answer": "操作反馈是当前覆盖最广的问题。", "tool": {"name": "rank_issues", "limit": 5},
    "evidence_issue_ids": [snapshot["issues"][0]["id"]], "caveat": snapshot["caveat"],
}, ensure_ascii=False)
assert app.validate_chatbi_answer(valid, snapshot)["tool"]["name"] == "rank_issues"
try:
    app.validate_chatbi_answer(valid.replace(snapshot["issues"][0]["id"], "not-real"), snapshot)
except ValueError:
    pass
else:
    raise AssertionError("Unknown evidence issue ID was accepted")

with tempfile.TemporaryDirectory() as folder:
    app.RUNTIME = Path(folder)
    app.DB = app.RUNTIME / "chatbi.sqlite3"
    app.init_db()
    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        with serve() as base:
            assert get(base, "/api/chatbi")["metrics"]["issues"] == 56
            req = Request(base + "/api/chatbi/ask", data=json.dumps({"question": "最严重的问题？"}).encode(),
                          headers={"Content-Type": "application/json", "X-InsightPulse-Token": app.TOKEN,
                                   "Origin": base})
            try:
                urlopen(req, timeout=10)
            except HTTPError as exc:
                error = json.load(exc)["error"]
                assert exc.code == 409 and error["code"] == "API_KEY_MISSING"
            else:
                raise AssertionError("ChatBI called the model without a configured API key")
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous

print("PASS: ChatBI snapshot, evidence, schema validation and API-key gate; no paid calls")
