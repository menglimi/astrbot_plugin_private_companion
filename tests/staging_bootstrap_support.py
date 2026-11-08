"""Synthetic configuration and fresh stores; no production host imports."""

import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS

from companion_v2.adapters.astrbot.native import HostPersona, persona_instruction
from companion_v2.adapters.astrbot.provider import UsageTariff, provider_fingerprint
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.adapters.astrbot.staging_bootstrap import AstrBotStagingBootstrap
from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES, StagingConfiguration
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import digest
from companion_v2.foundation.clock import DeterministicClock
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.model_gateway.models import Amount, BudgetPolicy
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.schema import REQUIRED_TABLES
from tests.native_adapter_support import FakeEvent, FakeHostContext, FakeProvider, FakeReverseApi
from tests.test_management_access import INSTALLATION, NOW, ROLE


def synthetic_configuration(native, **changes):
    config = StagingConfiguration(
        scope=ScopeRef(INSTALLATION, "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100",
                       "astrbot-conversation:conversation-1", "200"),
        host_id="host", ecosystem_id="companion",
        subject=SubjectRef(INSTALLATION, SubjectKind.HUMAN, "test-participant"),
        persona=HostPersona("bot-a", "original", ROLE, digest(persona_instruction(native.persona))),
        authority_ref="synthetic-local-administrator", valid_from=NOW, expires_at=None,
        capabilities=tuple(sorted(REQUIRED_CAPABILITIES)), provider_id="native-model",
        provider_fingerprint=provider_fingerprint(native.provider), tariff=UsageTariff(2000000, 1000000, 4000000),
        budget=BudgetPolicy(*(Amount(10**8, 10**9) for _ in range(5))),
        task_prompt="保持原角色，只使用有依据的材料。", max_output_tokens=100,
        policy=ConversationPolicy(0, 0, 180, 0))
    return replace(config, **changes)


def database_contents(runtime):
    return {table: tuple(tuple(row) for row in runtime.read_all(f'SELECT * FROM "{table}"', ()))
            for table in sorted(REQUIRED_TABLES)}


async def wait_for_batch(session, batch, *, timeout_seconds=20, clock=None):
    """Wait for a terminal batch state, driving capacity retries on a frozen clock.

    Other scheduled waits remain under the caller's control. The real-time budget
    bounds a hang; capacity retries use the injected clock and never pass the
    batch deadline.
    """
    application = session.application

    async def wait():
        while True:
            row = application.flow.row(batch)
            if row["state"] in ("complete", "blocked", "closed"):
                return
            if application.state == "faulted":
                raise AssertionError(application.failure_code)
            if clock is not None and row["reason"] == "capacity_wait":
                due_at = datetime.fromisoformat(row["due_at"])
                deadline_at = datetime.fromisoformat(row["deadline_at"])
                if due_at > deadline_at:
                    raise AssertionError("capacity retry exceeds batch deadline")
                if due_at > clock.now():
                    clock.set(due_at)
                    application.notify()
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout_seconds)


class BootstrapFixture:
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-v2-bootstrap-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data"
        self.native = FakeHostContext()
        self.clock = DeterministicClock.at(NOW)
        self.config = StagingConfiguration.parse(json.dumps(synthetic_configuration(self.native).document(), ensure_ascii=False))
        self.native_types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        self.runtime = self.open_runtime()
        self.bootstrap = self.make_bootstrap()

    def open_runtime(self):
        runtime = StorageRuntime.open(data_root=self.root, installation_id=INSTALLATION)
        self.addCleanup(runtime.close)
        return runtime

    def make_bootstrap(self, *, runtime=None, configuration=None):
        return AstrBotStagingBootstrap(runtime or self.runtime, self.native,
            configuration=configuration or self.config, native_types=self.native_types, now=self.clock.now)

    def load_session(self, bootstrap=None, *, instance="runtime-1"):
        session = (bootstrap or self.bootstrap).load(runtime_instance_id=instance).unwrap()
        self.addAsyncCleanup(session.close)
        return session

    async def start_session(self, session=None, *, generation=0):
        session = session or self.load_session()
        (await session.start(expected_task_generation=generation, expected_model_generation=generation)).unwrap()
        return session

    async def receive(self, session, *, event_id="event-1"):
        batch = (await session.receive(FakeEvent(self.native, event_id=event_id))).unwrap()
        await wait_for_batch(session, batch)
        return batch
