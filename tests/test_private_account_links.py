"""Revocable account associations keep the original route and old data untouched."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, SubjectKind
from companion_v2.relationships.account_links import PrivateAccountLinks, member_for
from companion_v2.storage.runtime import StorageRuntime

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def context(name, *, role="role-a", kind=ScopeKind.PRIVATE, installation="links-test"):
    subject = NS(subject_id="user-" + name, kind=SubjectKind.HUMAN)
    return NS(scope=NS(installation_id=installation, platform="platform-" + name, bot_id="bot-" + name,
        account_id="login-" + name, native_scope_id=name, kind=kind), actor=NS(subject=subject),
        role_binding=NS(role=NS(subject_id=role)), audience=NS(recipients=(subject,)))


class AccountLinkPersonaTests(unittest.TestCase):
    def test_only_one_explicit_same_role_persona_is_resolved(self):
        from companion_v2.relationships.account_link_context import persona_for
        member = member_for(context("a"))
        def settings(*ids):
            return NS(personas=[NS(bot_id="bot-a", role_id="role-a", persona_id=i) for i in ids])
        self.assertEqual(persona_for(settings("original"), member), "original")
        for ids in [(), ("[%None]",), ("original", "different")]:
            self.assertIsNone(persona_for(settings(*ids), member))

    def test_legacy_takeover_cannot_be_mistaken_for_original_route_sharing(self):
        from companion_v2.relationships.account_link_context import persona_for
        settings = NS(personas=[NS(bot_id="bot-a", role_id="role-a", persona_id="original")],
            window_links=[NS(mode="primary", primary_target_id="primary", alias_target_ids=("alias",))],
            private_target=lambda *args: NS(target_id="alias"))
        self.assertIsNone(persona_for(settings, member_for(context("a"))))


class AccountLinkStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temp.name), installation_id="links-test")
        self.addCleanup(self.runtime.close)
        self.links = PrivateAccountLinks(self.runtime)
        self.a, self.b, self.c = context("a"), context("b"), context("c")

    def save(self, members=None, *, revision=None, share=False, key="link-1", at=NOW, selected=None):
        return self.links.configure(selected or self.a, members or [self.a, self.b], persona_id="persona-a",
            expected_revision=self.links.read()["revision"] if revision is None else revision,
            share_context=share, operation_key=key, operator_id="dashboard-admin", at=at)

    def unlink(self, *, key="unlink-1", revision=None):
        return self.links.unlink(self.a, expected_revision=self.links.read()["revision"] if revision is None else revision,
            operation_key=key, operator_id="dashboard-admin", at=NOW)

    def weighted_save(self, weights, *, mode="random"):
        return self.links.configure(self.a, [self.a, self.b], persona_id="persona-a", expected_revision=0,
            share_context=False, operation_key="weighted", operator_id="admin", at=NOW,
            proactive={"mode": mode, "preferred": None, "weights": weights})

    def test_random_weights_roundtrip_and_replay(self):
        weights = [{"member": member_for(self.a), "weight": 3}, {"member": member_for(self.b), "weight": 1}]
        result = self.weighted_save(weights)
        self.assertEqual({row["member"]["subject_id"]: row["weight"] for row in result["group"]["proactive"]["weights"]},
                         {"user-a": 3, "user-b": 1})
        self.assertTrue(self.weighted_save(list(reversed(weights)))["replayed"])
        self.assertEqual(self.links.group_for(self.b)["proactive"], result["group"]["proactive"])

    def test_random_weights_reject_invalid_or_foreign_members(self):
        a, b, c = member_for(self.a), member_for(self.b), member_for(self.c)
        cases = [[], [{"member": a, "weight": 1}],
                 [{"member": a, "weight": 1}, {"member": a, "weight": 1}],
                 [{"member": a, "weight": 1}, {"member": c, "weight": 1}]]
        for bad in (-1, 1001, 1.5, True, "3", None):
            cases.append([{"member": a, "weight": bad}, {"member": b, "weight": 1}])
        cases.append([{"member": a, "weight": 0}, {"member": b, "weight": 0}])
        for weights in cases:
            with self.subTest(weights=weights), self.assertRaises(TaskError) as error:
                self.weighted_save(weights)
            self.assertEqual(error.exception.code, "account_link_weights_invalid")
            self.assertEqual(self.links.read()["revision"], 0)

    def test_zero_weight_allowed_if_another_is_positive(self):
        self.weighted_save([{"member": member_for(self.a), "weight": 0}, {"member": member_for(self.b), "weight": 1000}])
        self.assertIsNotNone(self.links.group_for(self.a))

    def test_default_is_no_association(self):
        self.assertEqual(self.links.status(self.a), {"revision": 0, "group": None})

    def test_association_alone_does_not_enable_sharing(self):
        result = self.save()
        self.assertFalse(result["group"]["share_context"])
        self.assertEqual(result["group"], self.links.group_for(self.b))

    def test_same_operation_replays_but_different_parameters_fail(self):
        self.save(revision=0)
        self.assertTrue(self.save(revision=0)["replayed"])
        with self.assertRaisesRegex(TaskError, "different parameters"):
            self.save(revision=0, share=True)

    def test_concurrent_stale_save_fails_closed(self):
        self.save()
        with self.assertRaises(TaskError) as error:
            self.save(revision=0, key="other")
        self.assertEqual(error.exception.code, "account_link_revision_conflict")

    def test_unlink_and_delayed_save_replay_do_not_recreate_group(self):
        self.save(revision=0)
        self.unlink()
        self.assertIsNone(self.save(revision=0)["group"])
        self.assertIsNone(self.links.group_for(self.b))

    def test_context_epoch_resets_on_reenable(self):
        first = self.save(share=True)["group"]["context_since"]
        self.save(share=False, key="off", at=NOW + timedelta(seconds=1))
        last = self.save(share=True, key="on", at=NOW + timedelta(seconds=2))["group"]["context_since"]
        self.assertGreater(last, first)

    def test_unchanged_save_keeps_context_epoch(self):
        first = self.save(share=True)["group"]["context_since"]
        last = self.save(share=True, key="again", at=NOW + timedelta(seconds=2))["group"]["context_since"]
        self.assertEqual(last, first)

    def test_adding_a_member_starts_new_consent_epoch(self):
        first = self.save(share=True)["group"]["context_since"]
        last = self.save([self.a, self.b, self.c], share=True, key="new", at=NOW + timedelta(seconds=2))
        self.assertGreater(last["group"]["context_since"], first)

    def test_cross_role_group_and_nonprivate_targets_are_rejected(self):
        for invalid in [context("other", role="role-b"), context("group", kind=ScopeKind.GROUP),
                        context("foreign", installation="foreign")]:
            with self.assertRaises(TaskError):
                self.save([self.a, invalid])

    def test_duplicate_and_missing_origin_are_rejected(self):
        for members in [[self.a, self.a], [self.b, self.c]]:
            with self.assertRaises(TaskError):
                self.save(members)

    def test_one_member_cannot_join_two_groups(self):
        self.save()
        with self.assertRaises(TaskError) as error:
            self.save([self.b, self.c], selected=self.c, key="other")
        self.assertEqual(error.exception.code, "account_link_already_linked")

    def test_boolean_revision_and_truthy_consent_are_rejected(self):
        for arguments in [{"revision": False}, {"share": "true"}]:
            with self.assertRaises(TaskError):
                self.save(**arguments)

    def test_persists_on_reopen_and_does_not_create_schema(self):
        before = [row["name"] for row in self.runtime.read_all("SELECT name FROM sqlite_master WHERE type='table'", ())]
        self.save(share=True)
        after = [row["name"] for row in self.runtime.read_all("SELECT name FROM sqlite_master WHERE type='table'", ())]
        self.assertEqual(before, after)
        self.runtime.close()
        self.runtime = StorageRuntime.open(data_root=Path(self.temp.name), installation_id="links-test")
        self.addCleanup(self.runtime.close)
        self.assertIsNotNone(PrivateAccountLinks(self.runtime).group_for(self.a))


if __name__ == "__main__":
    unittest.main()
