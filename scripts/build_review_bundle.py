"""Build and verify the credential-free, human-reviewed local MVP delivery."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import load_env
import os

if __name__ == "__main__":
    load_env()
    secret = os.environ.get("DEEPSEEK_API_KEY", "").encode()
    paths = [p for p in ROOT.iterdir() if p.is_file() and p.suffix in (".py", ".md")]
    for folder in ("scripts", "web", "docs", "data", "evaluation", "examples"):
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".json", ".txt", ".html", ".css", ".js"))
    paths = sorted(set(paths))
    blobs = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in paths}
    assert all(not secret or secret not in blob for blob in blobs.values()), "Credential detected; no bundle written"
    manifest = dict(status="local_mvp_human_review_required", files={name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()},
                    excluded=[".env", "runtime databases", "runtime exports", "logs", "Python caches"],
                    limits="Sample acceptance and reported human timing completed; not unattended AI or commercial validation. See docs/FINAL_ACCEPTANCE_RESULTS.md")
    destination = ROOT / "release/InsightPulse-local-MVP.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            archive.writestr(name, blob)
        archive.writestr("REVIEW_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with tempfile.TemporaryDirectory(prefix="insightpulse-review-") as folder:
        with zipfile.ZipFile(destination) as archive:
            assert archive.testzip() is None
            archive.extractall(folder)
        for name, digest in manifest["files"].items():
            assert hashlib.sha256((Path(folder) / name).read_bytes()).hexdigest() == digest
        for script in ("scripts/test_collect_sample.py", "test_pipeline.py", "scripts/test_demo_delivery.py", "scripts/score_holdout.py", "scripts/score_final_acceptance.py", "scripts/test_final_acceptance.py"):
            subprocess.run([sys.executable, script], cwd=folder, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(json.dumps(dict(path=str(destination), files=len(blobs), bytes=destination.stat().st_size,
                         sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), clean_extraction_checks="passed"), ensure_ascii=False))
