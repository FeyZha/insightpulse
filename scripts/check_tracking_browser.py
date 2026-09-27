"""Shared browser helpers and the current read-only portfolio UI check."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BROWSER = os.environ.get("INSIGHTPULSE_BROWSER") or next(
    (str(p) for p in (Path.home() / "AppData/Local/npm-cache/_npx").glob(
        "*/node_modules/agent-browser/bin/agent-browser-win32-x64.exe")), "agent-browser")
SESSION = f"tracking-replay-qa-{os.getpid()}"


def browser(*args):
    if args[0] in ("click", "fill", "select", "focus"):
        browser("scrollintoview", args[1])
    result = subprocess.run([BROWSER, "--session", SESSION, "--json", *args],
                            capture_output=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stderr or result.stdout
    value = json.loads(result.stdout)
    assert value["success"], value
    return value["data"]


def evaluate(code):
    return browser("eval", "--base64", base64.b64encode(code.encode()).decode())["result"]


def wait(code):
    browser("wait", "--fn", code)


def layout():
    value = evaluate("({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,errors:[...document.querySelectorAll('[role=alert],.state-box.error')].map(e=>e.textContent)})")
    assert value["scrollWidth"] <= value["width"], value
    assert not value["errors"], value
    return value


def main():
    from scripts.check_issue_tracking_browser import run
    run(ROOT / "runtime/tracking-browser")


if __name__ == "__main__":
    main()
