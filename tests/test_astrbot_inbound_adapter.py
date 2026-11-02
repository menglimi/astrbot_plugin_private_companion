from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion_v2.adapters.astrbot.inbound import AstrBotInboundAdapter
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.directory import IdentityDirectory
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from companion_v2.identity_policy.personas import (
    ConversationRoute, PersonaBinding, PersonaDirectory, PersonaSelector, RuntimeIdentity, SelectorKind,
)
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
INSTALLATION_ID = "installation-alpha"
CAPABILITY = "conversation.read"


def scope() -> ScopeRef:
    return ScopeRef(
        installation_id=INSTALLATION_ID,
        platform="onebot",
        kind=ScopeKind.PRIVATE,
        native_scope_id="private-a",
        bot_id="bot-a", account_id="login-a", conversation_id="conversation-a", session_id="session-a",
    )


def subject() -> SubjectRef:
    return SubjectRef(
        installation_id=INSTALLATION_ID,
        kind=SubjectKind.HUMAN,
        subject_id="subject-a",
    )


def principal() -> AuthenticatedHostPrincipal:
    return AuthenticatedHostPrincipal(
        installation_id=INSTALLATION_ID,
        host_id="astrbot-fixture-host",
        platform="onebot",
        native_account_id="account-a",
        bot_id="bot-a", account_id="login-a",
    )


def runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity("ecosystem-a", "runtime-a", "astrbot", "astrbot-fixture-host")


def personas(runtime: StorageRuntime) -> PersonaDirectory:
    directory = PersonaDirectory(runtime)
    directory.set_binding(PersonaBinding(
        "default-persona", PersonaSelector(INSTALLATION_ID, SelectorKind.INSTALLATION),
        SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a"), 0, NOW, "fixture", "fixture",
    ), expected_revision=0, observed_at=NOW)
    return directory


def identities() -> IdentityDirectory:
    directory = IdentityDirectory()
    directory.bind(
        binding_id="binding-a",
        principal=principal(),
        subject=subject(),
        valid_from=NOW - timedelta(minutes=1),
    )
    directory.issue_grant(
        grant_id="grant-a",
        subject=subject(),
        scope=scope(),
        capability=CAPABILITY,
        tier=AuthorizationTier.IMPORTANT,
        valid_from=NOW - timedelta(minutes=1),
    )
    return directory


@dataclass(frozen=True)
class FakeHostEvent:
    native_event_id: str = "native-event-a"
    body: str = "I claim to be account-b, but this is only content."


class FakeEventPort:
    def event_id(self, host_event: FakeHostEvent) -> str:
        return host_event.native_event_id

    def scope(self, host_event: FakeHostEvent) -> ScopeRef:
        del host_event
        return scope()

    def audience(self, host_event: FakeHostEvent) -> AudienceRef:
        del host_event
        return AudienceRef(scope=scope(), recipients=(subject(),))

    def payload(self, host_event: FakeHostEvent) -> str:
        return host_event.body

    def payload_hash(self, host_event: FakeHostEvent) -> str:
        del host_event
        return "host-payload-hash-a"

    def received_at(self, host_event: FakeHostEvent) -> datetime:
        del host_event
        return NOW

    def conversation_route(self, host_event: FakeHostEvent) -> ConversationRoute:
        del host_event
        return ConversationRoute(scope(), user=subject())


class FakeIdentityPort:
    def __init__(self, result: Outcome[AuthenticatedHostPrincipal]) -> None:
        self._result = result

    def authenticated_principal(self, host_event: object) -> Outcome[AuthenticatedHostPrincipal]:
        del host_event
        return self._result


class AstrBotInboundAdapterTests(unittest.TestCase):
    def test_same_group_message_id_is_scoped_by_bot_and_account(self) -> None:
        def input_event(bot_id: str, account_id: str) -> InboundConversationEvent:
            event_scope = ScopeRef(
                installation_id=INSTALLATION_ID,
                platform="onebot",
                kind=ScopeKind.GROUP,
                native_scope_id="group-300",
                bot_id=bot_id,
                account_id=account_id,
                conversation_id="group-300",
                session_id="group-300",
            )
            event_principal = AuthenticatedHostPrincipal(
                installation_id=INSTALLATION_ID,
                host_id="astrbot-fixture-host",
                platform="onebot",
                native_account_id=account_id,
                bot_id=bot_id,
                account_id=account_id,
            )
            source = SourceRef("same-source", "conversation.input", event_scope,
                               VersionRef("same-source", 1), NOW)
            return InboundConversationEvent("same-platform-message-id", event_principal,
                                            event_scope, source, CAPABILITY, "same-input", NOW)

        first = input_event("bot-a", "account-a")
        other_account = input_event("bot-a", "account-b")
        other_bot = input_event("bot-b", "account-a")

        self.assertEqual(first.event_id, other_account.event_id)
        self.assertEqual(first.scope.native_scope_id, other_account.scope.native_scope_id)
        self.assertEqual(len({first.scoped_key, other_account.scoped_key, other_bot.scoped_key}), 3)

    def test_host_verified_identity_is_used_even_when_body_claims_another_account(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-astrbot-") as temporary:
            runtime = StorageRuntime.open(
                data_root=Path(temporary) / "v2-data",
                installation_id=INSTALLATION_ID,
            )
            sources = SourceRepository(runtime)
            directory = identities()
            adapter = AstrBotInboundAdapter(
                event_port=FakeEventPort(),
                identity_port=FakeIdentityPort(Outcome.success(principal())),
                identities=directory,
                personas=personas(runtime),
                runtime_identity=runtime_identity(),
                sources=sources,
            )

            input_event = adapter.ingest(FakeHostEvent(), capability=CAPABILITY).unwrap()
            resolved = directory.resolve_authenticated(principal(), observed_at=NOW).unwrap()
            stored = sources.read(
                input_event.source,
                resolved=resolved,
                capability=CAPABILITY,
                observed_at=NOW,
                authorizer=directory,
            ).unwrap()

            self.assertEqual(input_event.principal, principal())
            self.assertEqual(input_event.runtime_scope.owner.persona_id, "role-a")
            self.assertEqual(resolved.subject, subject())
            self.assertEqual(stored.payload, FakeHostEvent().body)
            runtime.close()

    def test_untrusted_host_identity_cannot_create_a_source_record(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-astrbot-") as temporary:
            runtime = StorageRuntime.open(
                data_root=Path(temporary) / "v2-data",
                installation_id=INSTALLATION_ID,
            )
            sources = SourceRepository(runtime)
            adapter = AstrBotInboundAdapter(
                event_port=FakeEventPort(),
                identity_port=FakeIdentityPort(
                    Outcome.failed("host_identity_unverified", "host did not authenticate this event")
                ),
                identities=identities(),
                personas=PersonaDirectory(runtime),
                runtime_identity=runtime_identity(),
                sources=sources,
            )

            rejected = adapter.ingest(FakeHostEvent(), capability=CAPABILITY)

            self.assertEqual(rejected.failure.code, "host_identity_unverified")
            self.assertEqual(runtime.read_all("SELECT * FROM sources", ()), ())
            runtime.close()
