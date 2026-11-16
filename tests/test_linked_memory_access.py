"""Host memory consent and opaque, revocable read leases; no SDK or network."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests import test_private_account_links as fixtures
from tests.test_private_account_links import context, NOW
from companion_v2.relationships.account_link_memory import (
    CONFIRMATION, HISTORY_CONFIRMATION, read_scope,
)
from companion_v2.relationships.account_links import member_for
from companion_v2.adapters.astrbot.memory_providers import ExternalMemoryProviderRegistry
from companion_v2.execution.models import TaskError


class MemoryConsentTests(unittest.TestCase):
    setUp = fixtures.AccountLinkStoreTests.setUp
    save = fixtures.AccountLinkStoreTests.save

    def memory_save(self, enabled=True, history="since_enabled", confirmation=CONFIRMATION,
                    members=None, **kwargs):
        args = dict(persona_id="persona-a", expected_revision=self.links.read()["revision"],
                    share_context=False, operation_key="mem-" + str(self.links.read()["revision"]),
                    operator_id="admin", at=NOW, memory={"enabled": enabled, "history": history,
                    "confirmation": confirmation})
        args.update(kwargs)
        return self.links.configure(self.a, members or [self.a, self.b], **args)

    def test_memory_defaults_off_independently_of_context(self):
        self.save(share=True)
        self.assertEqual(self.links.group_for(self.a)["memory"], {"enabled": False, "history": "since_enabled"})

    def test_enable_and_old_history_require_distinct_confirmation(self):
        for history, confirmation in [("since_enabled", None), ("all", CONFIRMATION)]:
            with self.subTest(history=history), self.assertRaises(TaskError):
                self.memory_save(history=history, confirmation=confirmation)
        self.memory_save(history="all", confirmation=HISTORY_CONFIRMATION)
        self.assertTrue(self.links.group_for(self.a)["memory"]["enabled"])

    def test_policy_validation_is_strict(self):
        for value in ({"enabled": "yes", "history": "all"}, {"enabled": True, "history": "unknown"},
                      {"enabled": True, "history": "all", "members": []}):
            with self.assertRaises(TaskError):
                self.memory_save(memory=value)

    def test_added_account_disables_implicit_old_consent(self):
        self.memory_save()
        result = self.save([self.a, self.b, self.c], key="add")
        self.assertFalse(result["group"]["memory"]["enabled"])

    def test_added_account_requires_new_explicit_confirmation(self):
        self.memory_save()
        with self.assertRaises(TaskError):
            self.memory_save(members=[self.a, self.b, self.c], confirmation=None)

    def test_reenable_resets_cutoff_and_unchanged_save_preserves_it(self):
        first = self.memory_save()["group"]["memory_since"]
        same = self.memory_save(at=NOW + timedelta(seconds=1))["group"]["memory_since"]
        self.assertEqual(first, same)
        self.memory_save(enabled=False)
        last = self.memory_save(at=NOW + timedelta(seconds=2))["group"]["memory_since"]
        self.assertGreater(last, first)


class MemoryLeaseTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object()
        self.star = NS(star_cls=self.plugin, activated=True)
        self.native = NS(get_all_stars=lambda: [self.star])
        self.registry = ExternalMemoryProviderRegistry(self.native)
        self.addCleanup(self.registry.close)
        self.registry.register("test", plugin=self.plugin, read=lambda _: {}, summary=lambda _: {})
        self.entry = self.registry.select("read")
        self.scope = {"revision": 1, "members": ["synthetic"]}
        self.request = {"context": {"scope": "original"}, "purpose": "recall",
                        "source_plugin": "host", "schema": "v1"}

    def issue(self):
        return self.registry.issue_memory_read(self.entry, self.request, lambda: deepcopy(self.scope))

    def test_plain_json_cannot_grant_read_access(self):
        for token in (None, "forged", {}, object()):
            with self.assertRaises(TaskError):
                self.registry.authorize_memory_read({**self.request, "_memory_read_lease": token})

    def test_lease_bound_to_original_context_and_purpose(self):
        issued = self.issue()
        self.assertEqual(self.registry.authorize_memory_read(issued), self.scope)
        for field, value in (("purpose", "summary"), ("context", {}), ("source_plugin", "other")):
            with self.assertRaises(TaskError):
                self.registry.authorize_memory_read({**issued, field: value})

    def test_permission_or_revision_change_revokes_lease(self):
        issued = self.issue()
        self.scope["revision"] += 1
        with self.assertRaises(TaskError):
            self.registry.authorize_memory_read(issued)

    def test_expiry_and_unregister_revoke_lease(self):
        issued = self.issue()
        with patch("companion_v2.adapters.astrbot.memory_providers.monotonic", return_value=float("inf")):
            with self.assertRaises(TaskError):
                self.registry.authorize_memory_read(issued)
        self.registry.unregister(self.entry.registration_id)
        with self.assertRaises(TaskError):
            self.registry.authorize_memory_read(issued)

    def test_legacy_provider_cannot_receive_shared_scope(self):
        self.scope["group_id"] = "associated"
        with self.assertRaises(TaskError) as error:
            self.issue()
        self.assertEqual(error.exception.code, "linked_memory_provider_upgrade_required")
