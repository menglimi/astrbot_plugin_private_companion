"""Two native Bot accounts, two personas and synthetic private participants."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS

from tests import test_astrbot_staging_host as support
from tests.native_adapter_support import FakeEvent, FakePlatform, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_management_access import NOW
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.execution.models import TaskContext, stamp
from companion_v2.foundation.clock import DeterministicClock
from companion_v2.memory import codec
from astrbot_host.multi_controller import AstrBotMultiController


def multi_config(**changes):
    config = support.host_config(staging={"enabled": False})
    config["multi_host"] = {"enabled": True, "installation_id": "shared-test", "delivery_mode": "onebot_private",
        "targets": [dict(target_id="first", platform_id="qq-main", account_id="100", bot_id="bot-a", participant_id="200"),
                    dict(target_id="second", platform_id="qq-second", account_id="101", bot_id="bot-b", participant_id="200")],
        "personas": [dict(bot_id=bot, persona_id=persona, role_id=role)
                     for bot in ("bot-a", "bot-b") for persona, role in (("original", "role-a"), ("second", "role-b"))]}
    config["multi_host"].update(changes)
    return config


class MultiNativeContext(support.WebContext):
    def __init__(self):
        super().__init__()
        self.platforms = {"qq-main": self.platform, "qq-second": FakePlatform("qq-second")}
        self.persona_records = {"original": self.persona, "second": {"name": "second", "prompt": "你是第二个人格。"}}
        self.persona_manager.get_persona_v3_by_id = self.persona_records.get
        self.selections = {"qq-main": "original", "qq-second": "original"}
        self.conversations = {"qq-main": "conversation-a", "qq-second": "conversation-b"}

    def get_platform_inst(self, identifier):
        return self.platforms.get(identifier)

    async def current(self, umo):
        return self.conversations[umo.split(":", 1)[0]]

    async def resolve(self, **kwargs):
        self.role_lookups += 1
        if self.before_resolve:
            await self.before_resolve(self.role_lookups)
        selected = self.selections[kwargs["umo"].split(":", 1)[0]]
        return selected, self.persona_records.get(selected), None, False


class MultiFixture:
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-multi-host-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.native = MultiNativeContext()
        self.clock = DeterministicClock.at(NOW)
        self.window_personas = {
            "qq-main:FriendMessage:200": "original",
            "qq-second:FriendMessage:200": "original",
        }

        async def session_persona_override(umo):
            return self.window_personas.get(umo)

        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi,
                                        session_persona_override_lookup=session_persona_override)
        self.controller = self.make_controller()

    def make_controller(self, config=None):
        controller = AstrBotMultiController(self.native, config or multi_config(), self.root,
            native_types=self.types, now=self.clock.now)
        self.addAsyncCleanup(controller.terminate)
        return controller

    async def start_host(self):
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        await self.controller.session.account_recovery.refresh(wait=True)
        return self.controller.session.application

    def event(self, target="first", identifier="event-1", text="我不喝咖啡", **changes):
        configured = self.controller.settings.target(target)
        event = FakeEvent(NS(platform=self.native.platforms[configured.platform_id]), event_id=identifier,
                          sender=configured.participant_id, login=configured.account_id, text=text, **changes)
        self.window_personas.setdefault(event.unified_msg_origin, "original")
        event.created_at = self.clock.now().timestamp()
        return event

    async def accepted(self, target="first", identifier="event-1", text="我不喝咖啡"):
        result = await self.controller.receive_claimed(self.event(target, identifier, text))
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        app = self.controller.session.application
        await wait_for_batch(self.controller.session, batch)
        return TaskContext.restore(json.loads(app.messages.batch(batch)["context_json"])), batch

    def proposal_request(self, app, context, batch, *, key=None, pending=False):
        row = app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE batch_id = ?", (batch,))
        source = codec.source(json.loads(row["event_json"])["source"])
        wire = app.memory_commands
        return dict(schema_version=wire.schema_version, capability_id=wire.capability_id, capability_version="1.0",
            provider_id=wire.provider_id, provider_generation=wire.provider_generation,
            request_id="request", trace_id="trace",
            idempotency_key=key if key is not None else f"save:{batch}",
            deadline_at=stamp(self.clock.now() + timedelta(minutes=2)), scope=wire.scope_for_context(context),
            budget=dict(max_input_bytes=65536, max_output_bytes=65536, max_model_calls=0),
            payload=dict(namespace="personal", visibility="private", purpose="reply", proposal_id="proposal",
                operation="add", target_atom_id=None, expected_revision=None,
                subject=dict(kind="companion.person", ref=context.actor.subject.subject_id), predicate="饮用偏好",
                object="不喝咖啡", representation="用户不喝咖啡。", polarity="affirmed", attribution="user",
                confidence=1.0, retention="durable", evidence_refs=[wire.offer_source(context, source)],
                defer_until_boundary=pending))

    def remember(self, app, context, batch, **options):
        result = app.memory_commands.handle(context, self.proposal_request(app, context, batch, **options))
        self.assertIn(result["status"], ("pending", "succeeded"), result)
        return result["output"]
