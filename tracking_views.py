"""Comparison scopes, evidence and saved review settings. Read paths are offline."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse
from uuid import uuid4

import data_flow_v2 as flow
import pipeline
import tracking

METRICS = {"mentions": "全部提及", "complaints": "投诉表述", "improved": "明确改善"}
SCOPE_KEYS = {"before", "after", "mode", "metric", "language", "before_start", "before_end",
              "after_start", "after_end", "event_id", "offset", "analysis_version", "demo"}


def preferences(root):
    path = Path(root) / "runtime/tracking/preferences.json"
    return tracking._read(path) if path.exists() else {
        "events": [], "comparisons": [],
        "schedule": {"enabled": False, "interval_hours": 24, "analyze": False, "next_run_at": None}}


def write_preferences(root, value):
    path = Path(root) / "runtime/tracking/preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"preferences-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _text(value, maximum, label, optional=False):
    if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()):
        raise ValueError(f"{label}无效")
    return value.strip()


def event_window(event, days):
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError("观察周期须为 1–30 天")
    at = flow.timestamp(event["at"])
    start_after = at + (86400 if event["precision"] == "day" else 0)
    return {"before_start": pipeline.utc(at-days*86400), "before_end": pipeline.utc(at),
            "after_start": pipeline.utc(start_after), "after_end": pipeline.utc(start_after+days*86400)}


def catalog(root, state):
    return dict(languages=sorted({r["language"] for s in state["snapshots"] for r in s["reviews"].values()}))


def tracking_state(root, state):
    declarations = preferences(root).get("scope_declarations", [])
    snapshots = []
    for snap in state["snapshots"]:
        declaration = next((d for d in declarations if d.get("game") == state.get("game")
            and d.get("snapshot_id") == snap["id"] and d.get("source_hash") == snap["source_hash"]), None)
        snapshots.append(dict(snap, scope_declaration=declaration))
    return tracking.load_reanalysis(root, dict(state, snapshots=snapshots))


def select(state, request):
    if not isinstance(request, dict) or set(request) - SCOPE_KEYS:
        raise ValueError("比较范围包含未知字段")
    if any(not isinstance(value, str) for value in request.values()):
        raise ValueError("比较范围字段必须是字符串")
    scope = {"mode": "snapshots", "metric": "mentions", "language": "all", "offset": "480", **request}
    if scope.get("demo", "0") != "0":
        raise ValueError("回放选项无效")
    if scope["mode"] not in ("snapshots", "periods") or scope["metric"] not in METRICS:
        raise ValueError("比较视角或指标无效")
    offset = int(scope["offset"])
    if not -720 <= offset <= 840:
        raise ValueError("时区无效")
    if scope.get("analysis_version") not in (None, state["analysis_version"]):
        raise ValueError("保存的分析规则已变化，请重新选择范围并另存比较")
    scope["analysis_version"] = state["analysis_version"]
    originals = [tracking._snapshot(state, scope.get(side, "")) for side in ("before", "after")]
    if originals[0]["observed_at"] >= originals[1]["observed_at"]:
        raise ValueError("基准时点应早于对比时点")
    languages = {r["language"] for snap in originals for r in snap["reviews"].values()}
    if scope["language"] not in ("all", "chinese") and scope["language"] not in languages:
        raise ValueError("所选语言不在这些保存记录中")
    selected, reasons, ranges = [], [], []
    for side, original in zip(("before", "after"), originals):
        reviews = {rid: row for rid, row in original["reviews"].items()
                   if language_matches(row["language"], scope["language"])}
        snap = dict(original, reviews=reviews)
        if scope["mode"] == "periods":
            start, end = (flow.timestamp(scope.get(f"{side}_{key}", "")) for key in ("start", "end"))
            if not 0 < end-start <= 30*86400:
                raise ValueError("每段时期须为 1 秒至 30 天")
            ranges.append((start, end))
            snap["reviews"] = {rid: r for rid, r in reviews.items() if start <= r["created_at"] < end}
            label = "前期" if side == "before" else "后期"
            if end > time.time():
                reasons.append(f"{label}观察期尚未结束。")
            if original["observed_at"] < end:
                reasons.append(f"{label}记录未覆盖到所选截止时间；可使用实际保存时间重新比较。")
            elif original["observed_at"] > end:
                reasons.append(f"{label}记录保存于所选截止之后，仅供回看，不能代表当时原话。")
            if any(r["updated_at"] >= end for r in snap["reviews"].values()):
                reasons.append(f"{label}含截止后修改的评论，历史原话不完整。")
        selected.append(snap)
    if ranges:
        if abs((ranges[0][1]-ranges[0][0]) - (ranges[1][1]-ranges[1][0])) > .001:
            raise ValueError("请选择等长的两个时期")
        if ranges[1][0] < ranges[0][1]:
            raise ValueError("两个时期不能重叠，后期须晚于前期")
    return dict(state, snapshots=selected), scope, reasons


def source_breakdown(before, after, language="all"):
    """All collected identities, independent of AI coverage and issue attribution.

    For period comparisons this intentionally describes the two saved snapshots,
    NOT the two publication cohorts. The explicit basis/cutoffs travel with it.
    """
    left = {rid for rid, row in before["reviews"].items() if language_matches(row["language"], language)}
    right = {rid for rid, row in after["reviews"].items() if language_matches(row["language"], language)}
    members = {key: [] for key in ("shared", "first_seen", "new_review", "unknown_time", "not_seen",
                                  "scope_entered", "scope_left", "text_changed", "recommendation_changed")}
    for rid in sorted(left | right):
        kind = tracking.review_source(before, after, rid)
        if kind == "shared":
            if rid not in left:
                kind = "scope_entered"
            elif rid not in right:
                kind = "scope_left"
            else:
                a, b = before["reviews"][rid], after["reviews"][rid]
                if a["text"] != b["text"]:
                    members["text_changed"].append(rid)
                if a["recommended"] != b["recommended"]:
                    members["recommendation_changed"].append(rid)
        members[kind].append(rid)
    groups = {key: {"count": len(rows), "review_ids": rows} for key, rows in members.items()}
    counts = {key: len(rows) for key, rows in members.items()}
    summary = (f"后期 {len(right)} 条已采集评论中，共同评论 {counts['shared']} 条、"
               f"补采旧评论 {counts['first_seen']} 条、期间新评论 {counts['new_review']} 条。"
               f"共同评论中有 {counts['text_changed']} 条保存文本不同，不重复计入新增评论。")
    if counts["unknown_time"]:
        summary += f"另有 {counts['unknown_time']} 条发布时间缺失或异常，无法判定新旧。"
    if counts["scope_entered"] or counts["scope_left"]:
        summary += f"语言范围进入 {counts['scope_entered']} 条、移出 {counts['scope_left']} 条，不表示新发布或删除。"
    if counts["not_seen"]:
        summary += f"前期另有 {counts['not_seen']} 条在后期记录未见，不表示已删除。"
    return {"basis": "saved_snapshots", "language": language,
            "before_id": before["id"], "after_id": after["id"],
            "before_at": pipeline.utc(before["observed_at"]), "after_at": pipeline.utc(after["observed_at"]),
            "before_total": len(left), "after_total": len(right), "groups": groups, "summary": summary}


def comparison(state, request, events=()):
    selected, scope, reasons = select(state, request)
    event = next((e for e in events if e["id"] == scope.get("event_id")), None)
    if scope.get("event_id") and event is None:
        raise ValueError("未找到所选版本事件")
    if event and selected["snapshots"][0]["observed_at"] > flow.timestamp(event["at"]):
        reasons.append("前期记录不在版本发布之前，不能作为更新前基线。")
    result = tracking.compare(selected, scope["before"], scope["after"], scope["metric"], reasons=reasons)
    result.update(scope=scope, event=event, source_breakdown=source_breakdown(
        tracking._snapshot(state, scope["before"]), tracking._snapshot(state, scope["after"]), scope["language"]))
    return result


def evidence(state, scope, issue_id, side, offset=0, limit=10):
    if side not in ("before", "after"):
        raise ValueError("问题或证据侧别无效")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("证据分页无效")
    selected, scope, reasons = select(state, scope)
    result = tracking.compare(selected, scope["before"], scope["after"], scope["metric"], reasons=reasons)
    if not result["comparable"]:
        raise ValueError("暂不可比较：" + "；".join(result["reasons"]))
    if not any(row["id"] == issue_id for row in result["issues"]):
        raise ValueError("该问题不在当前比较中")
    snap = selected["snapshots"][0 if side == "before" else 1]
    rows = issue_evidence(state, snap, issue_id, scope["metric"])
    return dict(rows, items=rows["items"][offset:offset+limit], offset=offset,
                issue_id=issue_id, snapshot_id=snap["id"], observed_at=pipeline.utc(snap["observed_at"]))


def monitoring_evidence(state, snapshot_id, issue_id, offset=0, limit=5, *, report=None):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("证据分页无效")
    snap = tracking._snapshot(state, snapshot_id)
    report = tracking.monitoring(state) if report is None else report
    point = next(p for p in report["points"] if p["snapshot"]["id"] == snapshot_id)
    if point["reasons"]:
        raise ValueError("；".join(point["reasons"]))
    if issue_id not in point["values"]:
        raise ValueError("该问题不在当前监控定义中")
    rows = issue_evidence(state, snap, issue_id)
    return dict(rows, items=rows["items"][offset:offset+limit], offset=offset,
                snapshot_id=snapshot_id, observed_at=pipeline.utc(snap["observed_at"]))


def language_matches(language, selected):
    return selected == "all" or language == selected or (selected == "chinese" and language in ("schinese", "tchinese"))


def issue_evidence(state, snap, issue_id, metric="mentions"):
    """One evidence query for fixed pages and frozen assistant results."""
    rows = []
    for version in sorted(snap["reviews"].values(), key=lambda r: (r["created_at"], r["review_id"]), reverse=True):
        matches = [r for r in tracking.observations(state, version, snap) or [] if issue_id and r["issue_id"] == issue_id and
                   (metric == "mentions" or r["claim"] in
                    (("complaint", "worsened") if metric == "complaints" else ("improved",)))]
        if matches:
            rows.append({"review_id": version["review_id"], "text": version["text"],
                "text_zh": version.get("text_zh"), "translation_status": version.get("translation_status", "pending"),
                "recommended": version["recommended"], "created_at": pipeline.utc(version["created_at"]),
                "language": version["language"], "observations": matches,
                "updated_at": pipeline.utc(version["updated_at"]), "review_version_id": version["review_version_id"],
                "snapshot_id": snap["id"], "observed_at": pipeline.utc(snap["observed_at"])})
    source = snap.get("source_url", "")
    if urlparse(source).scheme not in ("http", "https"):
        source = ""
    return {"items": rows, "total": len(rows), "offset": 0, "source_url": source}


def timeline(state, request, issue_id=None, events=(), periods=False, measure="share", expected_hours=None, expected_since=None):
    if issue_id and issue_id not in state["issues"]:
        raise ValueError("未找到该问题")
    if measure not in ("share", "count"):
        raise ValueError("走势单位无效")
    selected, scope, _ = select(state, request)
    metric = scope["metric"]
    points, reasons = [], []
    if periods:
        comparison_result = comparison(state, request, events)
        reasons = comparison_result["reasons"]
        tz = timezone(timedelta(minutes=int(scope["offset"])))
        buckets = []
        for side, snap in zip(("before", "after"), selected["snapshots"]):
            start, end = (flow.timestamp(scope[f"{side}_{key}"]) for key in ("start", "end"))
            cursor = start
            while cursor < end:
                midnight = datetime.fromtimestamp(cursor, tz).replace(hour=0, minute=0, second=0, microsecond=0)
                stop = min((midnight + timedelta(days=1)).timestamp(), end)
                rows = {rid: r for rid, r in snap["reviews"].items() if cursor <= r["created_at"] < stop}
                buckets.append((side, cursor, stop, dict(snap, reviews=rows)))
                cursor = stop
        weekly = any(0 < len(s["reviews"]) < 30 for _, _, _, s in buckets)
        if weekly:
            buckets = []
            for side, snap in zip(("before", "after"), selected["snapshots"]):
                cursor, end = (flow.timestamp(scope[f"{side}_{key}"]) for key in ("start", "end"))
                while cursor < end:
                    stop = min(cursor+7*86400, end)
                    rows = {rid: r for rid, r in snap["reviews"].items() if cursor <= r["created_at"] < stop}
                    buckets.append((side, cursor, stop, dict(snap, reviews=rows)))
                    cursor = stop
        for side, start, end, snap in buckets:
            point = _point(state, snap, issue_id, metric, measure)
            point.update(at=pipeline.utc(start), end=pipeline.utc(end), side=side, snapshot_id=snap["id"])
            if any("少于 30" not in r for r in reasons):
                point.update(value=None, complete=False)
            points.append(point)
        # Explicit null separator preserves the version day/gap between disjoint windows.
        first_after = next((i for i, p in enumerate(points) if p["side"] == "after"), len(points))
        if first_after and first_after < len(points) and points[first_after-1]["end"] < points[first_after]["at"]:
            points.insert(first_after, {"at": points[first_after-1]["end"], "total": 0, "analyzed": 0,
                "count": None, "share": None, "value": None, "complete": False, "small": False})
        granularity = "week" if weekly else "day"
    else:
        baseline = selected["snapshots"][-1]
        for original in state["snapshots"]:
            if not selected["snapshots"][0]["observed_at"] <= original["observed_at"] <= baseline["observed_at"]:
                continue
            snap = dict(original, reviews={rid: r for rid, r in original["reviews"].items()
                if language_matches(r["language"], scope["language"])})
            point = _point(state, snap, issue_id, metric, measure)
            problems = tracking._reasons(state, [snap])
            if snap["scope"] != baseline["scope"]:
                problems.append("采集范围变化")
            if problems:
                point.update(value=None, complete=False)
                reasons.extend(problems)
            point.update(at=pipeline.utc(snap["observed_at"]), snapshot_id=snap["id"])
            points.append(point)
        granularity = "snapshot"
        if expected_hours:
            with_gaps = []
            for point in points:
                previous = flow.timestamp(with_gaps[-1]["at"]) if with_gaps else None
                if (previous is not None and (expected_since is None or previous >= flow.timestamp(expected_since))
                        and flow.timestamp(point["at"])-previous > expected_hours*5400):
                    with_gaps.append({"at": pipeline.utc(previous+expected_hours*3600),
                        "total": 0, "analyzed": 0, "count": None, "share": None, "value": None,
                        "complete": False, "small": False})
                    reasons.append("保存记录间隔超过采集计划，空缺期间的状态未知。")
                with_gaps.append(point)
            points = with_gaps
    return {"points": points, "events": list(events), "reasons": list(dict.fromkeys(reasons)),
            "granularity": granularity, "measure": measure, "issue_id": issue_id,
            "issues": [{"id": k, "title": v["title"]} for k, v in state["issues"].items()]}


def _point(state, snap, issue_id, metric, measure):
    stats, issues = tracking._stats(state, snap)
    key = "reviews" if metric == "mentions" else metric
    count = issues.get(issue_id, {}).get(key, 0) if issue_id else stats["recommended"]
    total = stats["analyzed"] if issue_id else stats["total"]
    share = tracking._percent(count, total)
    valid = stats["status"] == "complete" and total > 0
    return {"total": stats["total"], "analyzed": stats["analyzed"], "count": count, "share": share,
            "value": (count if measure == "count" else share) if valid else None,
            "complete": valid, "small": total < 30}


def signature(state, scope):
    selected, scope, _ = select(state, scope)
    return pipeline.fingerprint([tracking.COMPARISON_VERSION, state.get("game"), scope, state.get("reanalysis"),
        state["analysis_version"],
        [(s["id"], s["source_hash"], s.get("tracking_basis"), s.get("scope_declaration"),
          [{k: v for k, v in row.items() if k not in ("text_zh", "translation_status", "translation", "translated_evidence")}
           for _, row in sorted(s["reviews"].items())],
          [(rid, state["cache"].get(tracking._cache_key(row))) for rid, row in sorted(s["reviews"].items())])
         for s in selected["snapshots"]]])


def save(root, state, body):
    prefs = preferences(root)
    kind = body.get("kind")
    if kind == "event":
        name = _text(body.get("name"), 100, "版本名称")
        timestamp = flow.timestamp(body.get("at"))
        if not 0 < timestamp < 4102444800:
            raise ValueError("版本时间须在 1970–2100 年之间")
        at = pipeline.utc(timestamp)
        precision = body.get("precision", "time")
        if precision not in ("time", "day"):
            raise ValueError("时间精度无效")
        url = _text(body.get("url", ""), 2000, "公告地址", optional=True)
        if url and (urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname):
            raise ValueError("公告地址须为 HTTP 或 HTTPS 链接")
        event = {"id": uuid4().hex, "name": name, "at": at, "precision": precision, "url": url}
        prefs["events"].append(event)
    elif kind == "comparison":
        scope = body.get("scope")
        result = comparison(state, scope, prefs["events"])
        prefs["comparisons"].append({"id": uuid4().hex, "name": _text(body.get("name"), 100, "比较名称"),
            "scope": result["scope"], "created_at": pipeline.utc(time.time()), "signature": signature(state, scope)})
    elif kind == "schedule":
        enabled, analyze = body.get("enabled"), body.get("analyze", False)
        hours = body.get("interval_hours", 24)
        if type(enabled) is not bool or type(analyze) is not bool or type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError("定期采集设置无效，间隔须为 1–168 小时")
        if enabled and analyze and not os.environ.get("DEEPSEEK_API_KEY"):
            raise ValueError("自动补分析需要先配置 DeepSeek API 密钥")
        prefs["schedule"] = {**prefs["schedule"], "enabled": enabled, "analyze": analyze, "interval_hours": hours,
            "enabled_at": pipeline.utc(time.time()) if enabled else None,
            "next_run_at": pipeline.utc(time.time()+hours*3600) if enabled else None}
    elif kind == "delete_comparison":
        prefs["comparisons"] = [c for c in prefs["comparisons"] if c["id"] != body.get("id")]
    else:
        raise ValueError("未知的保存操作")
    write_preferences(root, prefs)
    return prefs


def saved_scope(root, state, saved_id):
    saved = next((c for c in preferences(root)["comparisons"] if c["id"] == saved_id), None)
    if not saved:
        raise ValueError("未找到保存的比较")
    if saved["signature"] != signature(state, saved["scope"]):
        raise ValueError("这份比较的分析结果已更新；请重新选择原范围并另存，不会替换旧比较")
    return saved["scope"]
