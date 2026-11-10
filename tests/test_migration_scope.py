import unittest

from companion_v2.legacy.migration_scope import (
    AUTO_SAME_ID, REVIEW_ACCOUNT_DIFFERS, REVIEW_AMBIGUOUS, REVIEW_GROUP_MISSING,
    REVIEW_PERSONA_OTHER_ROLE, REVIEW_PERSONA_UNASSIGNED, REVIEW_PLATFORM_DIFFERS,
    REVIEW_ROLE_MISSING, build_scope, map_groups, map_personas, needs_review,
)


def target(group_id, platform="qq-main", account="100", target_id=None, has_role=True):
    return {"target_id": target_id or "group-" + group_id, "group_id": group_id,
            "platform_id": platform, "account_id": account, "has_role": has_role}


class GroupScopeTests(unittest.TestCase):
    def test_same_platform_account_and_group_is_automatic(self):
        mapped = map_groups(["1097283005"], [target("1097283005")],
                            platform_id="qq-main", account_id="100")
        self.assertEqual(len(mapped), 1)
        self.assertTrue(mapped[0].auto)
        self.assertEqual(mapped[0].reason, AUTO_SAME_ID)
        self.assertEqual(mapped[0].target_id, "group-1097283005")

    def test_missing_target_group_is_left_for_review_not_guessed(self):
        mapped = map_groups(["555"], [target("1097283005")], platform_id="qq-main", account_id="100")
        self.assertFalse(mapped[0].auto)
        self.assertEqual(mapped[0].reason, REVIEW_GROUP_MISSING)
        self.assertIsNone(mapped[0].target_id)

    def test_platform_or_account_difference_is_reviewed(self):
        platform = map_groups(["1"], [target("1", platform="qq-other")],
                              platform_id="qq-main", account_id="100")[0]
        self.assertEqual(platform.reason, REVIEW_PLATFORM_DIFFERS)
        account = map_groups(["1"], [target("1", account="200")],
                             platform_id="qq-main", account_id="100")[0]
        self.assertEqual(account.reason, REVIEW_ACCOUNT_DIFFERS)

    def test_duplicate_group_numbers_need_an_exact_platform_account_match(self):
        targets = [target("1", account="100", target_id="a"), target("1", account="200", target_id="b")]
        exact = map_groups(["1"], targets, platform_id="qq-main", account_id="100")[0]
        self.assertTrue(exact.auto)
        self.assertEqual(exact.target_id, "a")
        ambiguous = map_groups(["1"], targets, platform_id="qq-main", account_id="999")[0]
        self.assertFalse(ambiguous.auto)
        self.assertEqual(ambiguous.reason, REVIEW_AMBIGUOUS)

    def test_target_without_a_role_cannot_receive_data(self):
        mapped = map_groups(["1"], [target("1", has_role=False)],
                            platform_id="qq-main", account_id="100")[0]
        self.assertFalse(mapped.auto)
        self.assertEqual(mapped.reason, REVIEW_ROLE_MISSING)

    def test_order_is_preserved_and_malformed_input_is_ignored(self):
        mapped = map_groups(["b", None, "", "a"], [target("a"), target("b")],
                            platform_id="qq-main", account_id="100")
        self.assertEqual([item.source_id for item in mapped], ["b", "a"])

    def test_review_list_keeps_only_unmapped_windows(self):
        mapped = map_groups(["a", "missing"], [target("a")], platform_id="qq-main", account_id="100")
        self.assertEqual([item.source_id for item in needs_review(mapped)], ["missing"])


class PersonaScopeTests(unittest.TestCase):
    def test_persona_already_owned_by_the_role_is_automatic(self):
        mapped = map_personas([{"persona_id": "astrbot"}], {"astrbot": "role-a"}, role_id="role-a")
        self.assertTrue(mapped[0].auto)
        self.assertEqual(mapped[0].target_id, "role-a")

    def test_unassigned_or_foreign_personas_are_never_dragged_in(self):
        unassigned = map_personas([{"persona_id": "x"}], {}, role_id="role-a")[0]
        self.assertFalse(unassigned.auto)
        self.assertEqual(unassigned.reason, REVIEW_PERSONA_UNASSIGNED)
        other = map_personas([{"persona_id": "x"}], {"x": "role-b"}, role_id="role-a")[0]
        self.assertFalse(other.auto)
        self.assertEqual(other.reason, REVIEW_PERSONA_OTHER_ROLE)

    def test_documents_are_json_safe(self):
        mapped = map_personas([{"persona_id": "x"}], {}, role_id="role-a")[0]
        self.assertEqual(mapped.document(), {"source_id": "x", "target_id": None,
                                             "auto": False, "reason": REVIEW_PERSONA_UNASSIGNED})


class ScopeDocumentTests(unittest.TestCase):
    def plan(self):
        return {
            "persona_sources": [{"persona_id": "astrbot", "label": "默认人格", "state": "ready"},
                                {"persona_id": "other", "label": "旧人格", "state": "ready"}],
            "group_networks": [{"source_group_id": "109", "member_count": 397, "edge_count": 80},
                               {"source_group_id": "555", "members": 283, "edges": 80}],
            "worldbook": {"groups": [{"source_group_id": "109", "entry_count": 1}],
                          "member_profile_groups": [{"source_group_id": "109", "member_count": 88}]},
            "private_contacts": {"total": 3, "rejected": [{"reason": "x"}]},
        }

    def test_preview_lists_what_will_move_and_what_asks_for_review(self):
        scope = build_scope([("astrbot", self.plan()), ("other", {"group_networks": [], "private_contacts": {}})],
                            [target("109"), target("915")], {"astrbot": "role-a"},
                            role_id="role-a", platform_id="qq-main", account_id="100")
        self.assertEqual([(item["persona_id"], item["source_id"]) for item in scope["groups"]],
                         [("astrbot", "109"), ("astrbot", "555")])
        self.assertTrue(scope["groups"][0]["auto"])
        self.assertEqual(scope["groups"][0]["target_id"], "group-109")
        self.assertEqual(scope["groups"][0]["counts"],
                         {"members": 397, "edges": 80, "knowledge_entries": 1, "member_profiles": 88})
        self.assertFalse(scope["groups"][1]["auto"])
        self.assertEqual(scope["groups"][1]["reason"], REVIEW_GROUP_MISSING)
        personas = {item["source_id"]: item for item in scope["personas"]}
        self.assertTrue(personas["astrbot"]["auto"])
        self.assertEqual(personas["other"]["reason"], REVIEW_PERSONA_UNASSIGNED)
        self.assertEqual(scope["counts"], {"personas": 2, "personas_auto": 1, "groups": 2,
                                           "groups_auto": 1, "review": 2})
        self.assertEqual(scope["private"], {"contacts": 3, "rejected": 1})
        self.assertEqual({item["kind"] for item in scope["review"]}, {"persona", "group"})

    def test_a_persona_outside_the_role_never_makes_its_groups_automatic(self):
        scope = build_scope([("other", self.plan())], [target("109")], {"astrbot": "role-a"},
                            role_id="role-a", platform_id="qq-main", account_id="100")
        self.assertEqual(scope["counts"]["groups_auto"], 0)
        self.assertEqual(scope["groups"][0]["reason"], REVIEW_PERSONA_UNASSIGNED)
        self.assertIsNone(scope["groups"][0]["target_id"])

    def test_review_token_tracks_the_reviewed_source(self):
        plans = lambda: [("astrbot", self.plan())]
        first = build_scope(plans(), [target("109")], {"astrbot": "role-a"},
                            role_id="role-a", platform_id="qq-main", account_id="100")
        again = build_scope(plans(), [target("109")], {"astrbot": "role-a"},
                            role_id="role-a", platform_id="qq-main", account_id="100")
        self.assertEqual(first["token"], again["token"])
        changed_plans = plans()
        changed_plans[0][1]["group_networks"][0]["member_count"] = 398
        changed = build_scope(changed_plans, [target("109")], {"astrbot": "role-a"},
                              role_id="role-a", platform_id="qq-main", account_id="100")
        self.assertNotEqual(first["token"], changed["token"])


if __name__ == "__main__":
    unittest.main()
