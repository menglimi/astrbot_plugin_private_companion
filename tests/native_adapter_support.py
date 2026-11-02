"""Network-free host doubles shared by the concrete adapter tests."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace as NS

from companion_v2.adapters.astrbot.native import HostAccount, HostPersona, NativePrivateBridge, persona_instruction
from companion_v2.adapters.astrbot.onebot import OneBotPrivateSender
from companion_v2.adapters.astrbot.provider import AstrBotTextProvider, UsageTariff
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.foreground import PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.execution.models import ProviderBinding, TaskContext, digest
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from companion_v2.model_gateway.models import Amount, BudgetPolicy, ModelEnvelope
from tests.test_host_application import HostFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE


def final_text(text="我在这里。", *, evidence=None):
    return json.dumps({"text": text, "claims": [{"id": "c1", "kind": "observed" if evidence else "subjective",
        "statement": text, "evidence": evidence}]}, ensure_ascii=False)


def completion(text=None, *, tools=None, usage=True, finish=None):
    return NS(id="response-1", choices=[NS(finish_reason=finish or ("tool_calls" if tools else "stop"),
        message=NS(content=text if text is not None else final_text(), tool_calls=tools))],
        usage=NS(prompt_tokens=30, completion_tokens=10, total_tokens=40,
                 prompt_tokens_details=NS(cached_tokens=10)) if usage else None)


class FakeClient:
    def __init__(self, state=None, max_retries=2):
        self.state = state or NS(calls=[], behavior=None)
        self.max_retries = max_retries

    def with_options(self, *, max_retries):
        return FakeClient(self.state, max_retries)


class FakeProvider:
    def __init__(self):
        self.client = FakeClient()
        self.provider_config = {"id": "native-model", "type": "openai_chat_completion",
                                "model": "fixture", "custom_extra_body": {"temperature": 0.7}}
        self.default_params = {"model", "messages", "tools", "tool_choice", "max_tokens", "max_completion_tokens"}
        self.model_name = "fixture"

    def get_model(self): return self.model_name

    async def _prepare_chat_payload(self, *, prompt, contexts, model):
        assert prompt is None
        return {"messages": deepcopy(contexts), "model": model}, deepcopy(contexts)

    async def text_chat(self, **_):
        raise AssertionError("native text_chat retry loop must not be called")

    async def _query(self, payload, tools, *, request_max_retries):
        assert request_max_retries == 1 and self.client.max_retries == 0
        self.client.state.calls.append((deepcopy(payload), tools))
        raw = await self.client.state.behavior(payload, tools) if self.client.state.behavior else completion()
        return await self._parse_openai_completion(raw, tools)

    async def _parse_openai_completion(self, raw, tools):
        calls = raw.choices[0].message.tool_calls or []
        arguments = []
        for call in calls:
            try:
                arguments.append(json.loads(call.function.arguments))
            except ValueError:
                arguments.append({})  # Match the installed host's repair behavior.
        return NS(raw_completion=raw, completion_text=raw.choices[0].message.content,
            tools_call_ids=[c.id for c in calls], tools_call_name=[c.function.name for c in calls], tools_call_args=arguments)


class FakeReverseApi:
    def __init__(self, clients):
        self._api_clients = clients
        self.calls = []
        self.result = {"message_id": 456}
        self.error = None

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.error:
            raise self.error
        return self.result


class FakePlatform:
    def __init__(self, platform_id="qq-main"):
        self.metadata = NS(name="aiocqhttp", id=platform_id)
        clients = {"100": object(), "101": object()}
        self.client = NS(_wsr_api_clients=clients, _api=NS(_wsr_api=FakeReverseApi(clients)))

    def meta(self): return self.metadata
    def get_client(self): return self.client


class FakeHostContext:
    def __init__(self):
        self.platform = FakePlatform()
        self.provider = FakeProvider()
        self.cid = "conversation-1"
        self.persona = {"name": "original", "prompt": "你是原来的角色，比折的陪伴者。"}
        self.selected = "original"
        self.agent_configuration = None
        self.role_lookups = 0
        self.before_resolve = None
        self.persona_manager = NS(resolve_selected_persona=self.resolve, personas_v3=[self.persona],
                                  get_persona_v3_by_id=self.persona_by_id)
        self.conversation_manager = NS(get_curr_conversation_id=self.current, get_conversation=self.conversation)

    def get_platform_inst(self, platform_id):
        return self.platform if self.platform.meta().id == platform_id else None

    def get_provider_by_id(self, provider_id): return self.provider if provider_id == "native-model" else None
    def get_config(self, *, umo):
        return deepcopy(self.agent_configuration) if self.agent_configuration is not None else {
            "provider_settings": {"default_personality": "original"}}
    async def current(self, umo): return self.cid

    def persona_by_id(self, persona_id):
        return next((persona for persona in self.persona_manager.personas_v3
                     if persona.get("name") == persona_id), None)

    async def conversation(self, umo, cid, *, create_if_not_exists):
        assert not create_if_not_exists
        return NS(persona_id="original")

    async def resolve(self, **kwargs):
        assert kwargs["platform_name"] == "aiocqhttp"
        self.role_lookups += 1
        if self.before_resolve:
            await self.before_resolve(self.role_lookups)
        return self.selected, self.persona, None, False


class FakeEvent:
    def __init__(self, context, *, event_id="event-1", sender="200", login="100", text="你好"):
        self.bot = context.platform.client
        self.platform_meta = context.platform.meta()
        kind = NS(value="FriendMessage")
        self.message_obj = NS(type=kind, group_id="", self_id=login, sender=NS(user_id=sender),
            message_id=event_id, message=[NS(type="Plain", text=text)])
        self.session = NS(message_type=kind, platform_name=self.platform_meta.id, platform_id=self.platform_meta.id, session_id=sender)
        self.unified_msg_origin = f"{self.platform_meta.id}:FriendMessage:{sender}"
        self.created_at = NOW.timestamp()
        self._extra = {}
        self._has_send_oper = False
        self.sent = []

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value

    async def send(self, message):
        self.sent.append(message)
        self._has_send_oper = True

    async def send_streaming(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        self._has_send_oper = True


class NativeFixture(HostFixture):
    def setUp(self):
        super().setUp()
        self.native = FakeHostContext()
        self.bind_native("100", "bot-a")
        self.bridge = self.make_bridge()
        self.native_sender = OneBotPrivateSender(self.bridge, binding=self.sender.binding, version="4.27.2",
                                                 reverse_api_type=FakeReverseApi, authorize=lambda: None,
                                                 now=self.clock.now)
        self.native_model = self.make_model()

    def bind_native(self, login, bot):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "qq-main", "200", bot, login)
        self.identity.bind(binding_id="native-" + login, principal=principal, subject=self.important.subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        return self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()

    def make_bridge(self, *, accounts=None, personas=None):
        return NativePrivateBridge(self.native, version="4.27.2", event_type=FakeEvent, installation_id=INSTALLATION,
            host_id="host", identities=self.identity, accounts=accounts or (HostAccount("qq-main", "100", "bot-a"),),
            personas=personas or (HostPersona("bot-a", "original", ROLE, digest(persona_instruction(self.native.persona))),))

    def map_second_native_persona(self, persona_id="second"):
        persona = {"name": persona_id, "prompt": "第二个人格提示词。"}
        self.native.persona_manager.personas_v3.append(persona)
        self.bridge.personas[("bot-a", persona_id)] = HostPersona(
            "bot-a", persona_id, ROLE, digest(persona_instruction(persona)))
        self.select_window_persona("original")

    def select_window_persona(self, persona_id):
        async def lookup(_umo):
            return persona_id

        self.bridge.session_persona_override_lookup = lookup

    def make_model(self, **changes):
        provider_type = changes.pop("provider_type", FakeProvider)
        return AstrBotTextProvider(self.native, bridge=self.bridge, binding=self.providers[0].binding,
            provider_id="native-model", version="4.27.2", provider_type=provider_type,
            function_tool_type=NS, tool_set_type=NS, tariff=UsageTariff(2000000, 1000000, 4000000),
            maximum_output_tokens=100, **changes)

    async def setup_host(self, **options):
        host = self.host(event_port=self.bridge.ports, identity_port=self.bridge.ports, role_port=self.bridge.ports,
            providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            budget=BudgetPolicy(*(Amount(10**8, 10**9) for _ in range(5))),
            policy=ConversationPolicy(0, 0, 180, 0), **options)
        return await self.start(host)

    async def capture(self, event=None):
        event = event or FakeEvent(self.native)
        captured = (await self.bridge.capture(event)).unwrap()
        actor = self.identity.resolve_authenticated(captured.principal, observed_at=NOW).unwrap()
        self.grant(actor, captured.route.scope, "text")
        return captured

    async def envelope(self, **changes):
        captured = await self.capture()
        self.personas.register_route(captured.route, observed_at=NOW)
        role = self.personas.resolve(captured.route.scope, observed_at=NOW).unwrap().binding
        source = SourceRef("input-1", "conversation.host_input", captured.route.scope, VersionRef("input-1", 1), NOW)
        event = InboundConversationEvent(captured.event_id, captured.principal, captured.route.scope, source,
                                         "text", digest(captured.text), NOW)
        request = PromptRequest(event, captured.instruction,
            SealedForegroundContext((PromptMaterial(source.owner, "current_source", captured.text, source),), (), ()),
            "只根据已见资料回复", role)
        actor = self.identity.resolve_authenticated(captured.principal, observed_at=NOW).unwrap()
        self.native_context = TaskContext(actor, role, captured.recipients)
        return replace(ModelEnvelope(request, "任务提示", 100, "configuration-1", before_dispatch=lambda: True), **changes)
