"""Offline checks for Review-local Observation alignment and matcher validation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observation_diff import compare_observations


def obs(oid, evidence, **overrides):
    row = dict(observation_id=oid, category="performance", object="game", symptom="stutter",
               description="进城卡顿", evidence=evidence, claim="complaint", change_evidence="")
    row.update(overrides)
    return row


def match(kind, before, after):
    return dict(before_ids=before, after_ids=after, kind=kind, reason="原文支持此判断",
                confirmed=kind != "unresolved")


class ObservationDiffChecks(unittest.TestCase):
    def test_saved_observations_ignore_order_and_preserve_inputs(self):
        before = [obs("b1", "stutter"), obs("b2", "slow loading", symptom="slow_loading")]
        after = [dict(before[1], observation_id="a2"), dict(before[0], observation_id="a1")]
        saved = deepcopy((before, after))
        result = compare_observations(before, after, "stutter, slow loading", "stutter, slow loading")
        self.assertEqual([row["kind"] for row in result], ["continued", "continued"])
        self.assertEqual((before, after), saved)
        self.assertEqual(result[0]["after"][0]["observation_id"], "a1")

    def test_explicit_improvement_is_a_state_change_not_new_observation(self):
        result = compare_observations([obs("b", "stutter")],
                                      [obs("a", "stutter fixed", claim="improved",
                                           change_evidence="stutter fixed")], "stutter", "stutter fixed")
        self.assertEqual(result[0]["kind"], "state_changed")
        self.assertEqual(result[0]["after"][0]["claim"], "improved")

    def test_synonyms_can_continue_through_explicit_matcher(self):
        before = [obs("b", "城里卡", object="town")]
        after = [obs("a", "进城不流畅", object="city", symptom="low_fps")]
        calls = []
        def matcher(*args):
            calls.append(args)
            return {"matches": [match("continued", ["b"], ["a"])]}
        result = compare_observations(before, after, "城里卡", "进城不流畅", matcher)
        self.assertEqual(result[0]["kind"], "continued")
        self.assertEqual(calls, [(before, after, "城里卡", "进城不流畅")])

    def test_different_symptoms_are_not_forced_together(self):
        before = [obs("b", "stutter")]
        after = [obs("a", "long loading", symptom="slow_loading")]
        result = compare_observations(before, after, "stutter", "long loading")
        self.assertEqual(result[0]["kind"], "unresolved")
        matcher = lambda *args: {"matches": [match("not_mentioned", ["b"], []), match("added", [], ["a"])]}
        result = compare_observations(before, after, "stutter", "long loading", matcher)
        self.assertEqual({row["kind"] for row in result}, {"not_mentioned", "added"})

    def test_changed_negation_evidence_is_not_treated_as_continued(self):
        before, after = [obs("b", "stutter")], [obs("a", "no stutter")]
        result = compare_observations(before, after, "stutter", "no stutter")
        self.assertEqual(result[0]["kind"], "unresolved")
        matcher = lambda *args: {"matches": [match("state_changed", ["b"], ["a"])]}
        result = compare_observations(before, after, "stutter", "no stutter", matcher)
        self.assertEqual(result[0]["kind"], "state_changed")

    def test_condition_change_needs_semantic_decision(self):
        before, after = [obs("b", "in town stutter")], [obs("a", "in forest stutter")]
        matcher = lambda *args: {"matches": [match("details_changed", ["b"], ["a"])]}
        result = compare_observations(before, after, "in town stutter", "in forest stutter", matcher)
        self.assertEqual(result[0]["kind"], "details_changed")

    def test_claim_change_with_changed_context_needs_confirmation(self):
        before = [obs("b", "city stutters")]
        after = [obs("a", "forest stutter fixed", description="森林卡顿改善", claim="improved",
                     change_evidence="forest stutter fixed")]
        result = compare_observations(before, after, "city stutters", "forest stutter fixed")
        self.assertEqual(result[0]["kind"], "unresolved")

    def test_category_changes_need_confirmation(self):
        before = [obs("b", "input freezes", category="controls_input")]
        after = [obs("a", "game freezes", category="stability")]
        result = compare_observations(before, after, "input freezes", "game freezes")
        self.assertEqual([row["kind"] for row in result], ["unresolved"])

    def test_ambiguous_split_keeps_every_piece_of_evidence(self):
        before = [obs("b", "stutter in city and forest")]
        after = [obs("a1", "city stutter", object="city"), obs("a2", "forest stutter", object="forest")]
        result = compare_observations(before, after, "stutter in city and forest", "city stutter, forest stutter")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["confirmed"])
        self.assertEqual(len(result[0]["after"]), 2)

    def test_empty_sides_mean_added_or_no_longer_mentioned(self):
        added = compare_observations([], [obs("a", "stutter")], "good", "stutter")
        removed = compare_observations([obs("b", "stutter")], [], "stutter", "good")
        self.assertEqual(added[0]["kind"], "added")
        self.assertEqual(removed[0]["kind"], "not_mentioned")
        self.assertIn("不能", removed[0]["reason"])
        self.assertEqual(compare_observations([], [], "good", "good"), [])

    def test_optional_mixed_state_is_preserved(self):
        before = [obs("b", "stutter fixed", claim="improved", change_evidence="stutter fixed", current_state="resolved")]
        after = [obs("a", "stutter fixed except city", claim="improved", change_evidence="stutter fixed", current_state="mixed")]
        result = compare_observations(before, after, "stutter fixed", "stutter fixed except city")
        self.assertEqual(result[0]["kind"], "state_changed")
        self.assertEqual(result[0]["after"][0]["current_state"], "mixed")

    def test_same_review_text_cannot_confirm_extraction_drift(self):
        result = compare_observations([obs("b", "stutter")], [], "stutter", "stutter")
        self.assertEqual(result[0]["kind"], "unresolved")

    def test_bad_matcher_payloads_raise_instead_of_losing_observations(self):
        before, after = [obs("b", "stutter")], [obs("a", "town stutter")]
        bad = [None, {"matches": []}, {"matches": [match("continued", ["unknown"], ["a"])]},
               {"matches": [match("continued", ["b"], ["a"]), match("added", [], ["a"])]},
               {"matches": [match("continued", ["b", "b"], ["a"])]},
               {"matches": [dict(match("unresolved", ["b"], ["a"]), confirmed=True)]},
               {"matches": [match("added", ["b"], ["a"])]},
               {"matches": [match("continued", [], [])]}]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                compare_observations(before, after, "stutter", "town stutter", lambda *args: payload)

    def test_invalid_observation_ids_and_evidence_raise(self):
        for items in ([obs("b", "not original")], [obs("b", "stutter"), obs("b", "stutter")]):
            with self.assertRaises(ValueError):
                compare_observations(items, [], "stutter", "good")

    def test_matcher_cannot_hide_claim_change_or_force_split(self):
        before = [obs("b", "stutter in city and forest")]
        after = [obs("a1", "city stutter fixed", object="city", claim="improved",
                     change_evidence="city stutter fixed"), obs("a2", "forest stutter", object="forest")]
        payloads = [
            {"matches": [match("continued", ["b"], ["a1", "a2"])]},
            {"matches": [match("continued", ["b"], ["a1"]), match("added", [], ["a2"])]},
        ]
        for payload in payloads:
            with self.assertRaises(ValueError):
                compare_observations(before, after, "stutter in city and forest",
                                     "city stutter fixed, forest stutter", lambda *args: payload)


if __name__ == "__main__":
    unittest.main()
