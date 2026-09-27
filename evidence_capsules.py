"""Deterministic, cached evidence summaries derived from an Issue."""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re

from vector_issue_experiment import cosine

CAPSULE_VERSION = "evidence-capsule-v1.2"
ALGORITHM_VERSION = "average-link-cosine-v1"
LABEL_VERSION = "representative-description-label-v1"
DEFAULT_SIMILARITY_THRESHOLD = 0.80
GENERIC_SYMPTOMS = {"", "bad", "bug", "issue", "other", "poor", "problem", "unknown"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def source_hash(issue, issue_group_version, embedding_meta, threshold):
    return _hash({
        "issue_id": issue["issue_id"],
        "issue_group_version": issue_group_version,
        "observation_ids": sorted(issue["observation_ids"]),
        "capsule_algorithm_version": ALGORITHM_VERSION,
        "label_version": LABEL_VERSION,
        "embedding_model": embedding_meta["model"],
        "embedding_input_format": embedding_meta.get("input_format", ""),
        "similarity_threshold": threshold,
    })


def readable_label(observation, limit=24):
    """Keep the representative's meaning while removing extraction boilerplate."""
    value = re.sub(r"^(?:玩家(?:发现|认为|表示|反馈|指出)|评论者(?:认为|表示))[:：]?", "",
                   observation.get("description", "").strip())
    value = re.sub(r"\s+", " ", value).strip(" ，。；：,.!！?？")
    if not value:
        value = " ".join(filter(None, (observation.get("object", ""), observation.get("symptom", ""))))
        value = value.replace("_", " ").strip()
    return value if len(value) <= limit else value[:limit - 1].rstrip(" ，。；：,") + "…"


def average_link_clusters(observation_ids, embeddings, threshold=DEFAULT_SIMILARITY_THRESHOLD):
    """Cut an average-link hierarchy at a cosine similarity threshold."""
    ids = sorted(observation_ids)
    if len(ids) != len(set(ids)) or not 0 <= threshold <= 1:
        raise ValueError("Observation IDs must be unique and threshold must be between 0 and 1")
    if any(oid not in embeddings for oid in ids):
        raise ValueError("Missing Observation embedding")
    clusters = {index: [oid] for index, oid in enumerate(ids)}
    similarities = {(left, right): cosine(embeddings[ids[left]], embeddings[ids[right]])
                    for left in range(len(ids)) for right in range(left + 1, len(ids))}
    next_id = len(ids)
    # ponytail: O(n^3) is fine for current <=131 observations per Issue; use scipy if this reaches thousands.
    while similarities:
        pair, score = min(similarities.items(), key=lambda item: (-item[1], item[0]))
        if score < threshold:
            break
        left, right = pair
        left_size, right_size = len(clusters[left]), len(clusters[right])
        others = sorted(set(clusters) - {left, right})
        merged_scores = {}
        for other in others:
            left_pair = tuple(sorted((left, other)))
            right_pair = tuple(sorted((right, other)))
            merged_scores[other] = ((left_size * similarities[left_pair])
                                     + (right_size * similarities[right_pair])) / (left_size + right_size)
        similarities = {pair: value for pair, value in similarities.items()
                        if left not in pair and right not in pair}
        clusters[next_id] = sorted(clusters.pop(left) + clusters.pop(right))
        for other, value in merged_scores.items():
            similarities[tuple(sorted((next_id, other)))] = value
        next_id += 1
    return sorted(clusters.values(), key=lambda members: (-len(members), members))


def _medoid(members, embeddings):
    if len(members) == 1:
        return members[0]
    scores = {oid: sum(cosine(embeddings[oid], embeddings[other]) for other in members if other != oid)
              / (len(members) - 1) for oid in members}
    return min(members, key=lambda oid: (-scores[oid], oid))


def _contradictory_reviews(observations):
    claims = defaultdict(set)
    for row in observations:
        claims[row["review_id"]].add(row.get("claim", "unknown"))
    return sum("improved" in values and bool(values.intersection({"complaint", "worsened"}))
               for values in claims.values())


def build_capsule(issue, observations, embeddings, issue_group_version, embedding_meta,
                  threshold=DEFAULT_SIMILARITY_THRESHOLD):
    by_id = {row["observation_id"]: row for row in observations}
    source_ids = list(issue["observation_ids"])
    if len(by_id) != len(observations) or set(source_ids) - set(by_id):
        raise ValueError("Issue observations are missing or duplicated")
    source_rows = [by_id[oid] for oid in source_ids]
    manifestations = []
    for members in average_link_clusters(source_ids, embeddings, threshold):
        representative_id = _medoid(members, embeddings)
        representative = by_id[representative_id]
        manifestations.append({
            "id": "m-" + _hash([issue["issue_id"], members])[:12],
            "label": readable_label(representative),
            "observation_count": len(members),
            "review_count": len({by_id[oid]["review_id"] for oid in members}),
            "observation_ids": members,
            "representatives": [{
                "observation_id": representative_id,
                "review_id": representative["review_id"],
                "description": representative["description"],
                "object": representative.get("object", ""),
                "symptom": representative.get("symptom", ""),
            }],
        })
    specific = sum(bool(row.get("object", "").strip())
                   and row.get("symptom", "").strip().lower() not in GENERIC_SYMPTOMS
                   for row in source_rows)
    largest = max((item["observation_count"] for item in manifestations), default=0)
    capsule = {
        "issue_id": issue["issue_id"],
        "capsule_version": CAPSULE_VERSION,
        "source_hash": source_hash(issue, issue_group_version, embedding_meta, threshold),
        "review_count": len({row["review_id"] for row in source_rows}),
        "observation_count": len(source_rows),
        "manifestations": manifestations,
        "evidence_quality": {
            "coverage": 1.0 if source_rows else 0.0,
            "specific_observation_share": round(specific / len(source_rows), 4) if source_rows else 0.0,
            "largest_manifestation_share": round(largest / len(source_rows), 4) if source_rows else 0.0,
            "unclustered_observations": sum(item["observation_count"] == 1 for item in manifestations),
            "minority_manifestations": sum(item["observation_count"] < largest for item in manifestations),
            "contradictory_review_count": _contradictory_reviews(source_rows),
        },
    }
    validate_capsule(capsule, issue, observations, issue_group_version, embedding_meta, threshold)
    return capsule


def validate_capsule(capsule, issue, observations, issue_group_version, embedding_meta,
                     threshold=DEFAULT_SIMILARITY_THRESHOLD):
    by_id = {row["observation_id"]: row for row in observations}
    source_ids = set(issue["observation_ids"])
    capsule_ids = [oid for item in capsule["manifestations"] for oid in item["observation_ids"]]
    if set(capsule_ids) != source_ids or len(capsule_ids) != len(set(capsule_ids)):
        raise ValueError("Capsule must contain every Issue Observation exactly once")
    if capsule["source_hash"] != source_hash(issue, issue_group_version, embedding_meta, threshold):
        raise ValueError("Capsule source hash is stale")
    if capsule["observation_count"] != len(source_ids):
        raise ValueError("Capsule Observation count is invalid")
    if capsule["review_count"] != len({by_id[oid]["review_id"] for oid in source_ids}):
        raise ValueError("Capsule Review count is invalid")
    if issue.get("observation_count", len(source_ids)) != capsule["observation_count"]:
        raise ValueError("Issue and Capsule Observation counts differ")
    if issue.get("review_count", capsule["review_count"]) != capsule["review_count"]:
        raise ValueError("Issue and Capsule Review counts differ")
    largest = max((item["observation_count"] for item in capsule["manifestations"]), default=0)
    if capsule["evidence_quality"].get("minority_manifestations") != sum(
            item["observation_count"] < largest for item in capsule["manifestations"]):
        raise ValueError("Capsule minority diagnostic is invalid")
    for item in capsule["manifestations"]:
        members = set(item["observation_ids"])
        if not 2 <= len(item["label"]) <= 24:
            raise ValueError("Manifestation label length is invalid")
        if item["observation_count"] != len(members):
            raise ValueError("Manifestation Observation count is invalid")
        if item["review_count"] != len({by_id[oid]["review_id"] for oid in members}):
            raise ValueError("Manifestation Review count is invalid")
        if any(row["observation_id"] not in members for row in item["representatives"]):
            raise ValueError("Representative does not belong to its Manifestation")


def cached_capsule(cache_dir, issue, observations, embeddings, issue_group_version, embedding_meta,
                   threshold=DEFAULT_SIMILARITY_THRESHOLD):
    path = Path(cache_dir) / f"{issue['issue_id']}.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            validate_capsule(value, issue, observations, issue_group_version, embedding_meta, threshold)
            return value, True
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    value = build_capsule(issue, observations, embeddings, issue_group_version, embedding_meta, threshold)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return value, False
