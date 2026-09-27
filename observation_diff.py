"""Compare complete, validated extractions of two versions of the SAME Review.

The caller guarantees Review identity and extraction completeness. Canonical keys
locate candidates; they do not establish that wording, conditions or state stayed
the same. Uncertain comparisons remain unresolved without a semantic matcher.
"""
from collections import defaultdict


KINDS = {"continued", "added", "not_mentioned", "state_changed", "details_changed", "unresolved"}
FIELDS = ("category", "object", "symptom", "description", "evidence", "claim", "change_evidence")


def _index(observations, text):
    if not isinstance(text, str) or not isinstance(observations, list):
        raise ValueError("Expected Review text and a complete Observation list")
    result = {}
    for item in observations:
        if not isinstance(item, dict):
            raise ValueError("Invalid Observation")
        oid = item.get("observation_id")
        if not isinstance(oid, str) or not oid.strip() or oid in result:
            raise ValueError("Observation IDs must be nonempty and unique within each version")
        if any(not isinstance(item.get(field), str) for field in FIELDS):
            raise ValueError("Observation fields must be strings")
        if any(not item[field].strip() for field in FIELDS if field != "change_evidence"):
            raise ValueError("Observation fields must not be empty")
        if item["claim"] not in ("complaint", "improved", "worsened"):
            raise ValueError("Invalid Observation claim")
        if item["evidence"] not in text or (item["change_evidence"] and item["change_evidence"] not in text):
            raise ValueError("Observation evidence must come from its Review version")
        if item["claim"] != "complaint" and not item["change_evidence"].strip():
            raise ValueError("Changed claim requires explicit change evidence")
        if "current_state" in item and (not isinstance(item["current_state"], str)
                                        or not item["current_state"].strip()):
            raise ValueError("Optional current_state must be a nonempty string")
        result[oid] = item
    return result


def _row(kind, before, after, reason):
    return dict(kind=kind, before=sorted(before, key=lambda item: item["observation_id"]),
                after=sorted(after, key=lambda item: item["observation_id"]),
                reason=reason, confirmed=kind != "unresolved")


def _same(left, right):
    return all(left.get(field) == right.get(field) for field in (*FIELDS, "current_state"))


def _key(item):
    return tuple(item[field].strip().lower() for field in ("category", "object", "symptom"))


def _state_changed(left, right):
    return left["claim"] != right["claim"] or (
        "current_state" in left and "current_state" in right
        and left["current_state"] != right["current_state"])


def _local(before, after, before_text, after_text):
    groups = defaultdict(lambda: [[], []])
    for side, items in enumerate((before, after)):
        for item in items:
            groups[item["category"]][side].append(item)
    result = []
    for category in sorted(groups):
        previous, current = groups[category]
        # Identical saved observations need no model call, regardless of ordering.
        for item in previous[:]:
            matches = [other for other in current if _same(item, other)]
            reverse_count = sum(_same(item, other) for other in previous)
            if len(matches) == reverse_count == 1:
                other = matches[0]
                result.append(_row("continued", [item], [other], "具体反馈及状态保持一致"))
                previous.remove(item)
                current.remove(other)
        if not previous or not current:
            for item in previous:
                result.append(_row("not_mentioned", [item], [], "本次不再提及，不能据此判断已解决"))
            for item in current:
                result.append(_row("added", [], [item], "本条评论新增具体反馈"))
        elif len(previous) == len(current) == 1 and _key(previous[0]) == _key(current[0]):
            if (before_text != after_text and previous[0]["description"] == current[0]["description"]
                    and _state_changed(previous[0], current[0])):
                result.append(_row("state_changed", previous, current, "同一具体反馈的明确表述状态改变"))
            else:
                result.append(_row("unresolved", previous, current, "已找到对应关注点，措辞、条件或状态仍待确认"))
        else:
            # ponytail: no synonym/negation heuristics; inject a semantic matcher when needed.
            result.append(_row("unresolved", previous, current, "对应关系未确认，可能涉及新反馈、改写或拆分合并"))
    unmatched_before = [item for row in result if row["kind"] == "not_mentioned" for item in row["before"]]
    unmatched_after = [item for row in result if row["kind"] == "added" for item in row["after"]]
    if unmatched_before and unmatched_after:
        result = [row for row in result if row["kind"] not in ("added", "not_mentioned")]
        result.append(_row("unresolved", unmatched_before, unmatched_after,
                           "类别或关注点变化，对应关系需结合前后原文确认"))
    return result


def _matched(payload, before, after, before_text, after_text):
    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        raise ValueError("Matcher must return a matches list")
    seen_before, seen_after, result = set(), set(), []
    for match in matches:
        if not isinstance(match, dict) or match.get("kind") not in KINDS:
            raise ValueError("Unknown Observation change kind")
        kind, reason = match["kind"], match.get("reason")
        if type(match.get("confirmed")) is not bool or match["confirmed"] != (kind != "unresolved"):
            raise ValueError("Unresolved matches must be unconfirmed; other kinds must be confirmed")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Matcher must explain each correspondence")
        sides = []
        for field, lookup, seen in (("before_ids", before, seen_before), ("after_ids", after, seen_after)):
            ids = match.get(field)
            if not isinstance(ids, list):
                raise ValueError("Matcher correspondence IDs must be lists")
            rows = []
            for oid in ids:
                if not isinstance(oid, str) or oid not in lookup or oid in seen:
                    raise ValueError("Matcher returned an unknown or duplicate Observation ID")
                seen.add(oid)
                rows.append(lookup[oid])
            sides.append(rows)
        previous, current = sides
        if not previous and not current:
            raise ValueError("Matcher returned an empty correspondence")
        expected = {"added": (0, 1), "not_mentioned": (1, 0), "continued": (1, 1),
                    "state_changed": (1, 1), "details_changed": (1, 1)}
        if kind in expected and (len(previous), len(current)) != expected[kind]:
            raise ValueError("Split/merged or ambiguous correspondences must remain unresolved")
        if previous and current and kind != "unresolved":
            if kind in ("continued", "details_changed") and _state_changed(previous[0], current[0]):
                raise ValueError("Matcher ignored an explicit Observation state change")
        if before_text == after_text and kind not in ("continued", "unresolved"):
            raise ValueError("Identical Review text cannot confirm a player feedback change")
        result.append(_row(kind, previous, current, reason.strip()))
    if seen_before != set(before) or seen_after != set(after):
        raise ValueError("Matcher omitted Observations")
    return result


def compare_observations(before, after, before_text, after_text, matcher=None):
    """Return complete Observation pairs/groups, without mutating either input.

    If local comparison is uncertain, matcher receives the FULL two lists and
    Review texts. It must return {"matches": [{"before_ids": [...],
    "after_ids": [...], "kind": ..., "reason": ..., "confirmed": bool}]} and
    cover each ID on each side exactly once. Only unresolved may contain multiple
    IDs per side. A bad response raises ValueError instead of silently dropping
    evidence. No network requests are made by this module.

    Optional current_state is compared only when both Observations supply it;
    the existing extraction schema does not produce it. No claim is promoted to
    verified resolution, and not_mentioned never means fixed.
    """
    previous, current = _index(before, before_text), _index(after, after_text)
    result = _local(before, after, before_text, after_text)
    if before_text == after_text:
        result = [row if row["kind"] == "continued" else
                  _row("unresolved", row["before"], row["after"], "原文未变，拆解差异不能视为玩家反馈变化")
                  for row in result]
    if matcher is not None and any(row["kind"] == "unresolved" for row in result):
        result = _matched(matcher(before, after, before_text, after_text), previous, current,
                          before_text, after_text)
    return sorted(result, key=lambda row: (
        tuple(item["observation_id"] for item in row["before"]),
        tuple(item["observation_id"] for item in row["after"])))
