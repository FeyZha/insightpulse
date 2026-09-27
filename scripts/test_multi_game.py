"""Read-only checks of the current second-game delivery, independent of collection date."""
from pathlib import Path
import sys
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import tracking
import tracking_views
snapshot=app.chatbi_snapshot("ostranauts")
overview=app.get_overview(snapshot)
raw=json.loads(app.chatbi_source("ostranauts").read_text(encoding="utf-8"))
source=json.loads(Path(raw["source"]["file"]).read_text(encoding="utf-8"))
assert overview["game"]["app_id"]==1022980
scope=overview["global"]["scope"]
assert scope["collected"]==len({r["review_id"] for r in source["reviews"]})
assert scope["analyzed"]+scope["pending"]+scope["failed"]+scope["excluded"]==scope["collected"]
assert scope["analyzed"]==scope["with_feedback"]+scope["without_feedback"]
assert all(issue["reviews"]==len(set(issue["review_ids"])) for issue in snapshot["issues"])
state=tracking_views.tracking_state(app.ROOT,app.product_state("ostranauts"))
catalog=tracking.catalog(state)
assert all(s["app_id"]==1022980 for s in state["snapshots"])
if state.get("reanalysis"):
    assert scope["pending"]==scope["failed"]==0
    assert catalog["status"]=="ready",catalog["reasons"]
    comparison=tracking_views.comparison(state,catalog["selection"])
    assert comparison["before"]["analyzed"]+comparison["before"].get("excluded",0)==comparison["before"]["total"]
    assert comparison["after"]["analyzed"]==scope["analyzed"]
    for side in ("before","after"):
        row=next(r for r in comparison["issues"] if r[side+"_count"])
        evidence=tracking_views.evidence(state,catalog["selection"],row["id"],side)
        assert evidence["items"][0]["snapshot_id"]==comparison[side]["id"]
        assert all(o["evidence"] in evidence["items"][0]["text"] for o in evidence["items"][0]["observations"])
print("Multi-game delivery passed:",scope)
