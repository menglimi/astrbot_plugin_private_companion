"""AstrBot 4.27.2/4.28.2 private capture, with explicit account/persona projections.

Native classes are supplied by trusted assembly. Importing V2 never boots AstrBot.
Async host reads finish before the SQLite intake transaction starts.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, _opaque
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.conversation.media.models import CapturedImage, MAX_INPUT_IMAGES, check_manifest, fits_budget
from companion_v2.conversation.quotes.models import CapturedQuote, message_number, event_images
from companion_v2.conversation.forwards.models import CapturedForward, ForwardIntakePolicy, fits_forwards
from companion_v2.conversation.media.audio.models import CapturedAudio, MAX_INPUT_AUDIOS, fits_audio_budget
from companion_v2.conversation.media.video.models import CapturedVideo, MAX_VIDEOS, fits_video_budget
from .files import file_locator, prepare_file_text, MAX_FILES


SUPPORTED_VERSIONS = frozenset(("4.27.2", "4.28.2", "4.29.0", "4.29.1"))
# Each entry above was exercised against that release's source. The version is
# never a gate: every host is allowed to start, the native symbols are imported
# eagerly, and a real surface change fails loudly at import instead of being
# guessed from a version string. An unreviewed host only earns one warning.
VERIFIED_SERIES = frozenset(("4.27", "4.28", "4.29"))
# Native routing is verified for the OneBot reverse-WS adapter and the two
# QQ Official adapter names AstrBot exposes.  Keeping the allow-list here
# prevents an arbitrary adapter from inheriting route privileges by accident.
SUPPORTED_PLATFORM_NAMES = frozenset(("aiocqhttp", "qq_official", "qq_official_webhook"))
OFFICIAL_PLATFORM_NAMES = frozenset(("qq_official", "qq_official_webhook"))
_REPORTED_UNVERIFIED = set()
NATIVE_LOOKUP_TIMEOUT_SECONDS = 20
NATIVE_LOOKUP_SLOW_SECONDS = 2
NATIVE_LOOKUP_SLOW_LOG_INTERVAL_SECONDS = 60
NATIVE_LOOKUP_STAGES = {
    "session_directory": "会话目录",
    "conversation_id": "当前会话编号",
    "session_settings": "会话服务设置",
    "persona": "会话生效人格",
    "conversation_verify": "会话切换校验",
}
logger = logging.getLogger(__name__)
_slow_lookup_log_state = {}


def report_slow_native_lookup(operation, elapsed_ms, *, details="", now=None):
    now = time.monotonic() if now is None else now
    state = _slow_lookup_log_state.get(operation)
    detail_suffix = " " + details if details else ""
    if state is None:
        logger.warning(
            "AstrBot native lookup slow operation=%s elapsed_ms=%d timeout_ms=%d outcome=ok occurrences=1%s",
            operation or "unknown", elapsed_ms, round(NATIVE_LOOKUP_TIMEOUT_SECONDS * 1000), detail_suffix,
        )
        _slow_lookup_log_state[operation] = {
            "emitted_at": now,
            "suppressed": 0,
            "max_elapsed_ms": elapsed_ms,
        }
        return

    state["suppressed"] += 1
    state["max_elapsed_ms"] = max(state["max_elapsed_ms"], elapsed_ms)
    if now - state["emitted_at"] < NATIVE_LOOKUP_SLOW_LOG_INTERVAL_SECONDS:
        return

    logger.warning(
        "AstrBot native lookup slow operation=%s elapsed_ms=%d max_elapsed_ms=%d timeout_ms=%d "
        "outcome=ok occurrences=%d suppressed_count=%d window_seconds=%d%s",
        operation or "unknown", elapsed_ms, state["max_elapsed_ms"],
        round(NATIVE_LOOKUP_TIMEOUT_SECONDS * 1000), state["suppressed"],
        state["suppressed"], NATIVE_LOOKUP_SLOW_LOG_INTERVAL_SECONDS, detail_suffix,
    )
    state.update(emitted_at=now, suppressed=0, max_elapsed_ms=0)


async def bounded_native_lookup(awaitable, *, operation="", report_slow=True):
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(awaitable, timeout=NATIVE_LOOKUP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as error:
        stage = NATIVE_LOOKUP_STAGES.get(operation)
        code = "host_native_lookup_timeout" + ("__" + operation if stage else "")
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        timeout_ms = round(NATIVE_LOOKUP_TIMEOUT_SECONDS * 1000)
        logger.warning("AstrBot native lookup timed out operation=%s elapsed_ms=%d timeout_ms=%d timeout_overrun_ms=%d",
                       operation or "unknown", elapsed_ms, timeout_ms, max(0, elapsed_ms - timeout_ms))
        raise TaskError(code, "AstrBot 读取" + (stage or "原生状态") + "超时，未取得可验证的结果。") from error
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if report_slow and elapsed_ms >= NATIVE_LOOKUP_SLOW_SECONDS * 1000:
        report_slow_native_lookup(operation, elapsed_ms)
    return result


MEDIA_PREPARE_TIMEOUT = 25


def _release_tuple(value):
    """Read major.minor[.patch] from a host version, ignoring pre-release tails."""
    parts = value.split("-", 1)[0].split("+", 1)[0].split(".")
    if len(parts) not in (2, 3) or any(not part.isdigit() or len(part) > 3 for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        numbers.append(0)
    return tuple(numbers)


def check_version(version):
    """Report an unreviewed host; never refuse to start one.

    The review list below only decides whether a warning is worth writing. A
    version string cannot prove the private surface still matches, and a wrong
    guess must not make an otherwise working install unusable: the native
    classes are imported eagerly, so a genuine API change fails at import with
    the exact missing symbol. Old, new and unparsable versions all continue.
    """
    if type(version) is str and version:
        release = _release_tuple(version)
        if version in SUPPORTED_VERSIONS or release is not None and "%d.%d" % release[:2] in VERIFIED_SERIES:
            return
        label = version
    else:
        label = "unknown"
    if label in _REPORTED_UNVERIFIED:
        return
    _REPORTED_UNVERIFIED.add(label)
    logger.warning("AstrBot %s is outside the reviewed native contract (%s); loading anyway, "
                   "the native imports decide whether this host still matches", label,
                   ", ".join(sorted(SUPPORTED_VERSIONS)))


def native_id(value):
    if type(value) not in (str, int):
        raise ValueError("native identifiers must be explicit strings or integers")
    return _opaque(str(value), field="native identifier")


def account_number(value):
    value = native_id(value)
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value or int(value) <= 0:
        raise ValueError("OneBot private account identifiers must be canonical positive numbers")
    return value


def route_id(value):
    """Validate one opaque platform route identifier.

    OneBot uses decimal QQ numbers, while QQ Official uses openids and the
    configured platform-instance id as its account key.  Route ids are stored
    in the ``platform:type:session`` origin, so colon, control and whitespace
    characters remain forbidden even though the value need not be numeric.
    """
    value = native_id(value)
    if len(value) > 128 or ":" in value or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("native route identifiers must be bounded opaque ids without separators")
    if value.isascii() and value.isdecimal() and int(value) <= 0:
        raise ValueError("numeric route identifiers must be positive numbers")
    return value


def supported_platform_name(value):
    return type(value) is str and value in SUPPORTED_PLATFORM_NAMES


def official_platform_name(value):
    return type(value) is str and value in OFFICIAL_PLATFORM_NAMES


def event_platform_name(event):
    return str(getattr(getattr(event, "platform_meta", None), "name", "") or "")


def event_platform_id(event):
    return str(getattr(getattr(event, "platform_meta", None), "id", "") or "")


def event_account_id(event):
    """Return the stable account key for one admitted native event.

    OneBot identifies a login by ``message.self_id``.  QQ Official exposes a
    synthetic self id on C2C/direct events, so the configured platform
    instance id is the stable account projection there.
    """
    message = getattr(event, "message_obj", None)
    name = event_platform_name(event)
    if official_platform_name(name):
        return route_id(event_platform_id(event))
    if name == "aiocqhttp":
        return account_number(getattr(message, "self_id", None))
    raise ValueError("unsupported native event platform")


def event_peer_id(event):
    sender = getattr(getattr(event, "message_obj", None), "sender", None)
    return route_id(getattr(sender, "user_id", None))


def event_group_id(event):
    return route_id(getattr(getattr(event, "message_obj", None), "group_id", None))


def native_user_subject_id(platform_name, sender_id):
    sender_id = route_id(sender_id)
    if platform_name == "aiocqhttp":
        return "onebot-user:" + sender_id
    if official_platform_name(platform_name):
        return "qq-official-user:" + sender_id
    raise ValueError("unsupported native user platform")


def native_client_connected(platform, account_id, client=None):
    """Whether one configured account currently has a usable native client."""
    if platform is None:
        return False
    try:
        meta = platform.meta()
        client = client if client is not None else platform.get_client()
    except (AttributeError, TypeError, ValueError):
        return False
    if client is None or not supported_platform_name(getattr(meta, "name", None)):
        return False
    if meta.name == "aiocqhttp":
        clients = getattr(client, "_wsr_api_clients", None)
        return isinstance(clients, dict) and bool(clients.get(str(account_id)))
    if getattr(client, "is_shutting_down", False):
        return False
    try:
        closed = getattr(client, "is_closed", None)
        if callable(closed) and closed():
            return False
    except Exception:
        return False
    return True


def persona_instruction(persona):
    """Retain the exact prompt and optional host-processed persona examples."""
    if persona is None:
        return ""
    prompt = persona.get("prompt")
    if not isinstance(prompt, str):
        raise TaskError("host_persona_invalid", "selected host persona has no text prompt")
    dialogs = persona.get("_begin_dialogs_processed") or []
    # AstrBot 4.29 给每条示例附了 ``_no_save`` 这类内部标记，宿主自己也不把它写回
    # 人格。按"必须恰好只有 role/content"判会把这些正常人格判成格式无法读取
    # （4.29.1 的现场就是保存映射失败、提示人格提示词或对话示例格式无法读取）。
    # 这里只要求 role/content 存在且可用，注入前再把多余字段剥掉。
    if not isinstance(dialogs, list) or any(
        not isinstance(d, dict) or d.get("role") not in ("user", "assistant")
        or not isinstance(d.get("content"), str) for d in dialogs
    ):
        raise TaskError("host_persona_invalid", "persona examples require the verified text format")
    cleaned = [{"role": d["role"], "content": d["content"]} for d in dialogs]
    if cleaned:
        prompt += "\n\n以下为人格的对话示例，用于保持说话风格，不代表本会话发生过的事情：\n" + encode(cleaned)
    return prompt


def configured_default_persona_id(context, umo):
    """Resolve the per-origin default persona selected by AstrBot's Agent Runner."""
    configuration = context.get_config(umo=umo)
    if not isinstance(configuration, dict):
        return "default"
    runner = configuration.get("agent_runner", {})
    runner_config = runner.get("config", {}) if isinstance(runner, dict) else {}
    if isinstance(runner_config, dict) and runner:
        if runner.get("runner_type") == "local":
            persona = runner_config.get("persona", {})
            selected = persona.get("persona_id", "default") if isinstance(persona, dict) else "default"
        else:
            selected = runner_config.get("persona_id", "default")
        return selected or "default"
    provider_settings = configuration.get("provider_settings", {})
    if not isinstance(provider_settings, dict):
        provider_settings = {}
    return provider_settings.get("default_personality") or "default"


@dataclass(frozen=True)
class HostAccount:
    platform_id: str
    self_id: str
    bot_id: str

    def __post_init__(self):
        _opaque(self.platform_id, field="platform instance")
        _opaque(self.bot_id, field="logical bot")
        if ":" in self.platform_id or route_id(self.self_id) != self.self_id:
            raise ValueError("account projection requires canonical host IDs")


@dataclass(frozen=True)
class HostPersona:
    bot_id: str
    persona_id: str | None
    role: SubjectRef
    instruction_hash: str

    def __post_init__(self):
        _opaque(self.bot_id, field="logical bot")
        _opaque(self.instruction_hash, field="persona instruction hash")
        if self.persona_id is not None:
            _opaque(self.persona_id, field="host persona")
        if self.role.kind is not SubjectKind.BOT:
            raise ValueError("host persona projection requires a bot role")


@dataclass(frozen=True)
class NativeImageLocator:
    position: int
    locator: str


@dataclass(frozen=True)
class NativeAudioLocator:
    position: int
    locator: str


@dataclass(frozen=True)
class NativeVideoLocator:
    position: int
    locator: str


@dataclass(frozen=True)
class NativeConversationInput:
    event_id: str
    principal: AuthenticatedHostPrincipal
    route: ConversationRoute
    recipients: AudienceRef
    text: str
    at: datetime
    role: SubjectRef
    instruction: str
    images: tuple[CapturedImage, ...] = ()
    quotes: tuple[CapturedQuote, ...] = ()
    forwards: tuple[CapturedForward, ...] = ()
    audios: tuple[CapturedAudio, ...] = ()
    videos: tuple[CapturedVideo, ...] = ()


NativePrivateInput = NativeConversationInput  # Preserve the private adapter's public snapshot name.


class NativeInputPorts:
    """Consume only frozen snapshots produced by the trusted native bridges."""

    def event_id(self, event): return event.event_id
    def scope(self, event): return event.route.scope
    def audience(self, event): return event.recipients
    def payload(self, event): return event.text
    def payload_hash(self, event):
        original = digest([event.text, tuple(i.descriptor for i in event.images)]) if event.images else digest(event.text)
        original = digest([original, tuple(a.descriptor for a in event.audios)]) if event.audios else original
        return digest([original, tuple(v.descriptor for v in event.videos)]) if event.videos else original
    def images(self, event): return event.images
    def quotes(self, event): return event.quotes
    def forwards(self, event): return event.forwards
    def audios(self, event): return event.audios
    def videos(self, event): return event.videos
    def received_at(self, event): return event.at
    def conversation_route(self, event): return event.route

    def authenticated_principal(self, event):
        if not isinstance(event, NativePrivateInput):
            return Outcome.failed("host_snapshot_required", "native events must be captured before intake")
        return Outcome.success(event.principal)

    def role_instruction(self, event, context):
        if context.role_binding.role != event.role:
            raise TaskError("host_persona_projection_mismatch", "V2 and the selected host persona must agree")
        return event.instruction


class NativePrivateBridge:
    scope_kind = ScopeKind.PRIVATE

    def __init__(self, context, *, version, event_type, installation_id, host_id, identities,
                 accounts: tuple[HostAccount, ...], personas: tuple[HostPersona, ...], image_type=None, image_loader=None,
                 reply_type=None, quote_loader=None, forward_type=None, forward_loader=None, now=None,
                 audio_type=None, audio_loader=None, component_reader=None, video_type=None, video_loader=None,
                 forward_intake=None, file_type=None, file_loader=None, file_settings=None,
                 session_persona_override_lookup=None, persona_snapshot_lookup=None, persona_selection_lookup=None,
                 persona_window_snapshot_lookup=None, persona_window_snapshot_update=None):
        check_version(version)
        self.context, self.event_type, self.identities = context, event_type, identities
        if session_persona_override_lookup is not None and not callable(session_persona_override_lookup):
            raise ValueError("session persona override lookup must be supplied by trusted host assembly")
        self.session_persona_override_lookup = session_persona_override_lookup
        if persona_snapshot_lookup is not None and not callable(persona_snapshot_lookup):
            raise ValueError("persona snapshot lookup must be supplied by trusted host assembly")
        self.persona_snapshot_lookup = persona_snapshot_lookup
        if persona_selection_lookup is not None and not callable(persona_selection_lookup):
            raise ValueError("persona selection lookup must be supplied by trusted host assembly")
        self.persona_selection_lookup = persona_selection_lookup
        if persona_window_snapshot_lookup is not None and not callable(persona_window_snapshot_lookup):
            raise ValueError("persona window snapshot lookup must be supplied by trusted host assembly")
        self.persona_window_snapshot_lookup = persona_window_snapshot_lookup
        if persona_window_snapshot_update is not None and not callable(persona_window_snapshot_update):
            raise ValueError("persona window snapshot updater must be supplied by trusted host assembly")
        self.persona_window_snapshot_update = persona_window_snapshot_update
        self.installation_id, self.host_id = installation_id, host_id
        if (image_type is None) != (image_loader is None) or (image_type is not None and (
                not isinstance(image_type, type) or not callable(image_loader))):
            raise ValueError("image intake needs an explicit native type and a trusted loader")
        self.image_type, self.image_loader = image_type, image_loader
        if (reply_type is None) != (quote_loader is None) or (reply_type is not None and (
                not isinstance(reply_type, type) or not callable(quote_loader))):
            raise ValueError("quote intake needs an explicit native reply type and a trusted loader")
        self.reply_type, self.quote_loader = reply_type, quote_loader
        if (forward_type is None) != (forward_loader is None) or (forward_type is not None and (
                not isinstance(forward_type, type) or not callable(forward_loader))):
            raise ValueError("forward intake needs an explicit native type and a trusted loader")
        self.forward_type, self.forward_loader = forward_type, forward_loader
        # Intake ceilings default to the structural constants, so an installation that
        # never narrows them behaves exactly as before.
        if forward_intake is None:
            forward_intake = ForwardIntakePolicy()
        if not isinstance(forward_intake, ForwardIntakePolicy):
            raise ValueError("forward intake policy must be the declared structural type")
        self.forward_intake = forward_intake
        if (audio_type is None) != (audio_loader is None) or (audio_type is not None and (
                not isinstance(audio_type, type) or not callable(audio_loader))):
            raise ValueError("audio intake needs an explicit native type and a trusted loader")
        if component_reader is not None and not callable(component_reader):
            raise ValueError("component reader must be supplied by trusted host assembly")
        self.audio_type, self.audio_loader, self.component_reader = audio_type, audio_loader, component_reader
        if (video_type is None) != (video_loader is None) or (video_type is not None and (
                not isinstance(video_type, type) or not callable(video_loader))):
            raise ValueError("video intake needs an explicit native type and a trusted loader")
        self.video_type, self.video_loader = video_type, video_loader
        if (file_type is None) != (file_loader is None) or (file_type is not None and (
                not isinstance(file_type, type) or not callable(file_loader))):
            raise ValueError("file intake needs an explicit native type and a trusted loader")
        if file_settings is not None and not callable(file_settings):
            raise ValueError("file preferences must come from trusted host assembly")
        self.file_type, self.file_loader, self.file_settings = file_type, file_loader, file_settings
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.accounts = {(a.platform_id, a.self_id): a for a in accounts}
        self.personas = {(p.bot_id, p.persona_id): p for p in personas}
        if not accounts or len(self.accounts) != len(accounts) or len(self.personas) != len(personas):
            raise ValueError("host projections must be explicit and unique")
        if any(p.role.installation_id != installation_id for p in personas):
            raise ValueError("persona projections cross installation")
        self.platforms = {}
        for account in accounts:
            platform = context.get_platform_inst(account.platform_id)
            if platform is None:
                raise TaskError("host_platform_unavailable", "the configured native platform has not loaded")
            meta = platform.meta()
            if not supported_platform_name(meta.name) or meta.id != account.platform_id:
                raise TaskError("host_platform_changed", "the configured platform does not match its native account")
            self.platforms[account.platform_id] = (platform, platform.get_client())
        self.ports = NativeInputPorts()

    def account(self, scope):
        """The registered account a private scope belongs to, or an explicit refusal."""
        account = self.accounts.get((scope.platform, scope.account_id))
        if (account is None or scope.installation_id != self.installation_id or scope.bot_id != account.bot_id
                or scope.kind is not self.scope_kind):
            raise TaskError("host_account_changed", "original host account projection is unavailable")
        return account

    def platform(self, scope):
        account = self.account(scope)
        platform, client = self.platforms[account.platform_id]
        meta = platform.meta()
        if (self.context.get_platform_inst(account.platform_id) is not platform
                or platform.get_client() is not client or meta.id != account.platform_id
                or not supported_platform_name(meta.name)):
            raise TaskError("host_account_changed", "original platform/client was replaced")
        return platform

    def platform_name(self, scope_or_account):
        platform_id = getattr(scope_or_account, "platform_id", None) or getattr(scope_or_account, "platform", None)
        platform = self.platforms.get(platform_id, (None, None))[0]
        return str(platform.meta().name) if platform is not None else ""

    def account_connected(self, account, client=None):
        platform = self.platforms.get(account.platform_id, (None, None))[0]
        return native_client_connected(platform, account.self_id, client)

    def client(self, scope):
        platform = self.platform(scope)
        account = self.account(scope)
        client = platform.get_client()
        if not self.account_connected(account, client):
            raise TaskError("host_account_changed", "original native account is no longer connected")
        return client

    @staticmethod
    def _umo(platform_id, sender_id):
        return f"{platform_id}:FriendMessage:{sender_id}"

    @staticmethod
    def _conversation_key(umo, conversation_id):
        return "astrbot-conversation:" + (native_id(conversation_id) if conversation_id is not None else "unopened-" + digest(umo))

    @staticmethod
    def _role_conversation_id(conversation_id, role):
        return conversation_id

    def _persona_projection(self, account, persona_id):
        if persona_id is not None and type(persona_id) is not str:
            return None
        projection = self.personas.get((account.bot_id, persona_id))
        if projection is not None:
            return projection
        mappings = [item for (bot_id, _), item in self.personas.items() if bot_id == account.bot_id]
        if len(mappings) == 1 and mappings[0].persona_id == "[%None]":
            projection = mappings[0]
            return (projection if persona_id in (None, "[%None]")
                    else replace(projection, persona_id=persona_id))
        return None

    async def _role(self, account, sender_id):
        umo = self._umo(account.platform_id, sender_id)
        conversation_id, role, instruction = await self._role_for_umo(account, umo)
        return self._role_conversation_id(conversation_id, role), role, instruction

    async def _selected_persona(self, account, umo, *, fresh_selection=False):
        """Resolve the V2 override or AstrBot's active persona through a trusted mapping."""
        manager = self.context.conversation_manager
        cid = await bounded_native_lookup(manager.get_curr_conversation_id(umo), operation="conversation_id")
        projected_ids = tuple(persona_id for bot_id, persona_id in self.personas if bot_id == account.bot_id)
        if not projected_ids:
            raise TaskError("host_persona_unmapped", "this Bot has no V2 persona mapping")
        follows_window_persona = len(projected_ids) == 1 and projected_ids[0] == "[%None]"

        snapshot = None
        if self.persona_window_snapshot_lookup is not None:
            snapshot = self.persona_window_snapshot_lookup(umo, account.self_id)
            if inspect.isawaitable(snapshot):
                snapshot = await bounded_native_lookup(snapshot, operation="persona_snapshot", report_slow=False)
        snapshot_persona = (snapshot.get("persona_id") if isinstance(snapshot, dict)
            and snapshot.get("bot_id") == account.bot_id
            and self._persona_projection(account, snapshot.get("persona_id")) is not None
            and (snapshot.get("conversation_id") == cid
                 or snapshot.get("selection_source") == "verified")
            else None)
        window_snapshot_used = snapshot_persona is not None and not fresh_selection
        used_override = False
        if window_snapshot_used:
            # A verified window binding belongs to the UMO, not to AstrBot's
            # replaceable conversation row. Conversation reset/recreation must
            # keep the window's role until an explicit refresh rechecks the
            # native persona selection.
            selected = snapshot_persona
        else:
            override = None
            if self.session_persona_override_lookup is not None:
                override = await bounded_native_lookup(
                    self.session_persona_override_lookup(umo), operation="session_settings")
            if override is not None:
                if (type(override) is not str or not override.strip() or len(override) > 120
                        or any(ord(char) < 32 for char in override)):
                    raise TaskError("host_persona_invalid", "the V2 window persona override is invalid")
                selected = override
                used_override = True
            else:
                if self.persona_snapshot_lookup is not None:
                    conversation_persona_id = await bounded_native_lookup(
                        self.persona_snapshot_lookup(self.context, umo, cid),
                        operation="persona", report_slow=False)
                elif cid is not None:
                    conversation = await bounded_native_lookup(
                        manager.get_conversation(umo, cid, create_if_not_exists=False), operation="conversation")
                    if conversation is None:
                        raise TaskError("host_conversation_missing", "selected host conversation is unavailable")
                    conversation_persona_id = getattr(conversation, "persona_id", None)
                else:
                    conversation_persona_id = None
                if conversation_persona_id is not None and type(conversation_persona_id) is not str:
                    raise TaskError("host_persona_invalid", "selected host conversation has an invalid persona")
                lookup = self.persona_selection_lookup
                if lookup is not None:
                    resolved = lookup(self.context, umo, conversation_persona_id)
                    selected = (await bounded_native_lookup(resolved, operation="persona")
                                if inspect.isawaitable(resolved) else resolved)
                else:
                    selected = conversation_persona_id
                    if selected != "[%None]" and not selected:
                        selected = configured_default_persona_id(self.context, umo)
            if selected not in projected_ids and not (follows_window_persona and selected not in (None, "[%None]")):
                raise TaskError(
                    "host_persona_window_unbound",
                    "AstrBot's selected persona is not mapped to this V2 Bot; refresh the window snapshot",
                )

        if selected == "[%None]":
            persona = None
        elif selected is None:
            persona = None
        else:
            resolver = getattr(self.context.persona_manager, "get_persona_v3_by_id", None)
            if callable(resolver):
                persona = resolver(selected)
            else:
                persona = next((item for item in getattr(self.context.persona_manager, "personas_v3", ())
                                if isinstance(item, dict) and item.get("name") == selected), None)
            if persona is None:
                raise TaskError("host_persona_unavailable", "the V2 persona mapping no longer exists in AstrBot")
            if persona.get("name") != selected:
                raise TaskError("host_persona_invalid", "AstrBot returned a different persona than the V2 mapping")
        projection = self._persona_projection(account, selected)
        if (projection is not None and self.persona_window_snapshot_update is not None
                and not used_override):
            self.persona_window_snapshot_update(
                umo, account.self_id, account.bot_id, selected, projection.role.subject_id, cid)
        special = False
        if (not window_snapshot_used
                and await bounded_native_lookup(manager.get_curr_conversation_id(umo),
                                                operation="conversation_verify") != cid):
            raise TaskError("host_conversation_changed", "host conversation changed during persona lookup")
        return cid, selected, persona, special

    async def _selected_role_snapshot(self, account, umo):
        cid, selected, persona, special = await self._selected_persona(account, umo)
        projection = self._persona_projection(account, selected)
        if special or projection is None or (persona is None and selected not in (None, "[%None]")):
            raise TaskError("host_persona_unmapped", "selected persona needs an explicit V2 projection")
        if persona is not None and persona.get("name") != selected:
            raise TaskError("host_persona_invalid", "host returned a different persona")
        instruction = persona_instruction(persona)
        return cid, selected, persona, projection, instruction

    def _confirm_persona_projection(self, account, selected, projection, instruction):
        if self.persona_window_snapshot_lookup is not None:
            return projection
        fingerprint = digest(instruction)
        if fingerprint != projection.instruction_hash:
            projection = replace(projection, instruction_hash=fingerprint)
            if ((account.bot_id, selected) in self.personas
                    or (account.bot_id, "[%None]") not in self.personas):
                self.personas[(account.bot_id, selected)] = projection
        return projection

    async def _role_for_umo(self, account, umo):
        cid, selected, persona, projection, instruction = await self._selected_role_snapshot(account, umo)
        self._confirm_persona_projection(account, selected, projection, instruction)
        return self._conversation_key(umo, cid), projection.role, instruction

    async def persona_snapshot(self, scope, *, persona_id=None):
        """The host persona text behind one private scope, for the workshop page.

        Deliberately narrower than ``_role``: it neither compares the text against
        the pinned projection nor returns ``instruction_hash`` (a security-relevant
        value that never reaches a page). A persona that already drifted from the
        pinned profile is *exactly* when an operator needs to look at it -- the
        workshop exists to help them reconcile the two texts -- so failing the
        digest check here would hide the page at the moment it matters.

        The text is returned verbatim, trailing whitespace and host-processed
        examples included: "the left pane is the original" has to stay literally
        true, so nothing here may trim or normalise it. An installation with no
        resolved persona yields ``resolved`` False and empty text, which the page
        renders as a state rather than as a failure.
        """
        account = self.account(scope)
        sender = route_id(scope.native_scope_id)
        umo = self._umo(account.platform_id, sender)
        conversation_manager = self.context.conversation_manager
        conversation_id = await bounded_native_lookup(
            conversation_manager.get_curr_conversation_id(umo), operation="conversation_id")
        if persona_id is None:
            override = None
            if self.session_persona_override_lookup is not None:
                override = await bounded_native_lookup(
                    self.session_persona_override_lookup(umo), operation="session_settings")
            if override is not None:
                if (type(override) is not str or not override.strip() or len(override) > 120
                        or any(ord(char) < 32 for char in override)):
                    raise TaskError("host_persona_invalid", "the V2 window persona override is invalid")
                selected = override
                if selected == "[%None]":
                    persona = None
                else:
                    manager = self.context.persona_manager
                    resolver = getattr(manager, "get_persona_v3_by_id", None)
                    persona = (resolver(selected) if callable(resolver) else next((item for item in
                        getattr(manager, "personas_v3", ())
                        if isinstance(item, dict) and item.get("name") == selected), None))
                    if persona is None or persona.get("name") != selected:
                        raise TaskError("host_persona_unavailable", "the V2 window persona no longer exists in AstrBot")
            else:
                manager = self.context.persona_manager
                if self.persona_snapshot_lookup is not None:
                    conversation_persona_id = await bounded_native_lookup(
                        self.persona_snapshot_lookup(self.context, umo, conversation_id), operation="persona")
                elif conversation_id is not None:
                    conversation = await bounded_native_lookup(
                        conversation_manager.get_conversation(umo, conversation_id, create_if_not_exists=False),
                        operation="persona")
                    if conversation is None:
                        raise TaskError("host_conversation_missing", "selected host conversation is unavailable")
                    conversation_persona_id = getattr(conversation, "persona_id", None)
                else:
                    conversation_persona_id = None
                settings = self.context.get_config(umo=umo).get("provider_settings", {})
                resolver = getattr(manager, "resolve_selected_persona", None)
                if callable(resolver):
                    selected, persona, _, special = await bounded_native_lookup(resolver(
                        umo=umo, conversation_persona_id=conversation_persona_id,
                        platform_name=self.platform_name(account), provider_settings=settings), operation="persona")
                    if special:
                        selected, persona = None, None
                else:
                    selected = conversation_persona_id
                    getter = getattr(manager, "get_persona_v3_by_id", None)
                    persona = (getter(selected) if callable(getter) and selected else next((item for item in
                        getattr(manager, "personas_v3", ())
                        if isinstance(item, dict) and item.get("name") == selected), None))
        else:
            if type(persona_id) is not str or not persona_id.strip() or persona_id == "[%None]":
                raise TaskError("host_persona_invalid", "a concrete AstrBot persona must be selected")
            manager = self.context.persona_manager
            resolver = getattr(manager, "get_persona_v3_by_id", None)
            if callable(resolver):
                persona = resolver(persona_id)
            else:
                persona = next((item for item in getattr(manager, "personas_v3", ())
                                if isinstance(item, dict) and item.get("name") == persona_id), None)
            selected = persona_id
            if persona is None:
                raise TaskError("host_persona_unavailable", "the selected AstrBot persona no longer exists")
        current_id = await bounded_native_lookup(conversation_manager.get_curr_conversation_id(umo),
                                                 operation="conversation_verify")
        if current_id != conversation_id:
            raise TaskError("host_conversation_changed", "host conversation changed during persona lookup")
        if persona is not None and persona.get("name") != selected:
            raise TaskError("host_persona_invalid", "host returned a different persona")
        name = persona.get("name") if persona is not None else ""
        return {"persona_id": "" if selected in (None, "[%None]") else selected,
                "name": name if isinstance(name, str) else "",
                "text": persona_instruction(persona), "resolved": persona is not None}

    async def verify_role(self, scope, binding, instruction=None):
        self.client(scope)
        sender = route_id(scope.native_scope_id)
        if scope.session_id != sender or binding.scope != scope:
            raise TaskError("host_route_changed", "original private session is inconsistent")
        cid, role, prompt = await self._role(self.accounts[(scope.platform, scope.account_id)], sender)
        self.client(scope)
        if role != binding.role:
            raise TaskError("host_persona_changed", "host role differs from the original turn")
        return prompt

    def _verified_persona_umo(self, scope, binding, account):
        sender = route_id(scope.native_scope_id)
        if scope.session_id != sender or binding.scope != scope:
            raise TaskError("host_route_changed", "original private session is inconsistent")
        return self._umo(account.platform_id, sender)

    async def verified_persona(self, scope, binding):
        account = self.account(scope)
        self.client(scope)
        umo = self._verified_persona_umo(scope, binding, account)
        cid, selected, _, projection, prompt = await self._selected_role_snapshot(account, umo)
        projection = self._confirm_persona_projection(account, selected, projection, prompt)
        if projection.role != binding.role:
            raise TaskError("host_persona_changed", "selected host persona differs from the verified role")
        self.client(scope)
        return {"persona_id": projection.persona_id, "prompt": prompt,
                "bot_id": scope.bot_id, "account_id": scope.account_id}

    async def _capture_images(self, locators):
        images = []
        for locator in locators:
            image = await self.image_loader(locator)
            if not isinstance(image, CapturedImage) or image.descriptor.position != locator.position:
                raise TaskError("host_image_invalid", "image loader did not preserve the original component position")
            images.append(image)
            check_manifest(tuple(i.descriptor for i in images))
        return tuple(images)

    async def capture(self, event):
        intake = self.forward_intake
        try:
            from .quotes import NativeQuoteLocator, image_locator
            from .forwards import NativeForwardLocator, resource_id
            from companion_v2.conversation.quotes.models import event_audios, event_videos
            if not isinstance(event, self.event_type):
                raise TaskError("host_event_unsupported", "this profile accepts native private events")
            message, meta, session = event.message_obj, event.platform_meta, event.session
            platform_id = meta.id
            if (getattr(message.type, "value", None) != "FriendMessage"
                    or getattr(session.message_type, "value", None) != "FriendMessage" or message.group_id):
                raise TaskError("host_scene_unsupported", "group and thread adapters are not installed")
            sender, login = event_peer_id(event), event_account_id(event)
            account = self.accounts.get((platform_id, login))
            if (account is None or not supported_platform_name(meta.name) or session.platform_id != platform_id
                    or session.platform_name != platform_id or session.session_id != sender
                    or event.unified_msg_origin != self._umo(platform_id, sender)):
                raise TaskError("host_route_mismatch", "event fields do not identify one registered private route")
            parts = self.component_reader(event) if self.component_reader is not None else tuple(message.message)
            if not parts or len(parts) > 128:
                raise TaskError("host_media_unsupported", "message components are empty or exceed the intake budget")
            text_parts, locators, quote_locators, forward_locators, audio_locators, video_locators = [], [], [], [], [], []
            file_locators = []
            onebot = meta.name == "aiocqhttp"
            for position, part in enumerate(parts):
                kind = getattr(part.type, "value", part.type)
                if kind == "Plain" and isinstance(part.text, str):
                    text_parts.append(part.text)
                elif kind == "At":
                    # QQ Official prepends a synthetic At for C2C events. It is
                    # routing metadata, not user-visible text.
                    continue
                elif kind == "Image" and self.image_type is not None and type(part) is self.image_type:
                    locators.append(image_locator(position, part.file, part.url))
                    text_parts.append("\n[图片 " + str(len(locators)) + "]\n")
                elif kind == "Reply" and self.reply_type is not None and type(part) is self.reply_type:
                    if onebot:
                        quote_locators.append(NativeQuoteLocator(position, message_number(part.id)))
                    text_parts.append("\n[引用消息]\n")
                elif kind == "Forward" and self.forward_type is not None and type(part) is self.forward_type:
                    if onebot:
                        forward_locators.append(NativeForwardLocator(position, resource_id(part.id)))
                    text_parts.append("\n[合并转发 " + str(len(forward_locators)) + "]\n")
                elif kind == "Record" and self.audio_type is not None and type(part) is self.audio_type:
                    value = part.file if type(part.file) is str and part.file.startswith("base64://") else part.url or part.file
                    if type(value) is not str or not value:
                        raise TaskError("host_audio_unavailable", "voice has no original host attachment")
                    audio_locators.append(NativeAudioLocator(position, value))
                    text_parts.append("\n[语音 " + str(len(audio_locators)) + "]\n")
                elif kind == "Video" and self.video_type is not None and type(part) is self.video_type:
                    value = part.file if type(part.file) is str and part.file.startswith("base64://") else part.url or part.file
                    if type(value) is not str or not value:
                        raise TaskError("host_video_unavailable", "video has no original host attachment")
                    video_locators.append(NativeVideoLocator(position, value))
                    text_parts.append("\n[视频 " + str(len(video_locators)) + "]\n")
                elif kind == "File" and self.file_type is not None and type(part) is self.file_type:
                    if len(file_locators) < MAX_FILES:
                        file_locators.append(file_locator(position, part))
                    text_parts.append("\n[文件附件，正文尚未读取]\n")
                else:
                    raise TaskError("host_media_unsupported", "this component needs its own governed source adapter")
            if len(locators) > MAX_INPUT_IMAGES:
                raise TaskError("host_image_limit", "too many images in one input")
            if len(video_locators) > MAX_VIDEOS:
                raise TaskError("host_video_limit", "too many video attachments in one input")
            if len(audio_locators) > MAX_INPUT_AUDIOS:
                raise TaskError("host_audio_limit", "too many voice attachments in one input")
            if len(quote_locators) > 1:
                raise TaskError("host_quote_unsupported", "one original reply component is supported per input")
            if len(forward_locators) > intake.max_forwards:
                raise TaskError("host_forward_limit", "too many forwarded records in one input")
            text = "".join(text_parts)
            if not text.strip() or len(text) > 65536:
                raise TaskError("host_text_invalid", "private input must be bounded nonempty text")
            event_id = native_id(message.message_id)
            at = datetime.fromtimestamp(event.created_at, timezone.utc)
            principal = AuthenticatedHostPrincipal(self.installation_id, self.host_id, platform_id, sender, account.bot_id, login)
            actor = require(self.identities.resolve_authenticated(principal, observed_at=at))
            # Freeze all event fields before any host await; later event middleware cannot restamp them.
            event_client = event.bot
            cid, role, instruction = await self._role(account, sender)
            scope = ScopeRef(self.installation_id, platform_id, ScopeKind.PRIVATE, sender, account.bot_id, login, cid, sender)
            if event_client is not self.client(scope):
                raise TaskError("host_client_mismatch", "event did not originate from the registered account client")
            frozen_images, frozen_quotes, frozen_forwards = tuple(locators), tuple(quote_locators), tuple(forward_locators)
            frozen_audios, frozen_videos = tuple(audio_locators), tuple(video_locators)
            frozen_files = tuple(file_locators)
            from companion_v2.conversation.media.understanding import settings as understanding_settings
            file_preferences = understanding_settings(self.file_settings(scope, role) if self.file_settings else {})
            async def prepare():
                images = await self._capture_images(frozen_images)
                audios = []
                for locator in frozen_audios:
                    audio = await self.audio_loader(locator)
                    if type(audio) is not CapturedAudio or audio.descriptor.position != locator.position:
                        raise TaskError("host_audio_invalid", "audio loader changed the original component position")
                    audios.append(audio)
                    if not fits_audio_budget(tuple(a.descriptor for a in audios), tuple(i.descriptor for i in images)):
                        raise TaskError("host_audio_limit", "voice and images exceed the shared intake budget")
                quotes = []
                for locator in frozen_quotes:
                    if locator.message_id == event_id:
                        raise TaskError("host_quote_invalid", "an input cannot quote itself")
                    quote = await self.quote_loader(locator, client=event_client, scope=scope,
                                                    intake=intake)
                    if (not isinstance(quote, CapturedQuote) or quote.position != locator.position
                            or quote.message_id != locator.message_id
                            or quote.sender_id not in (sender, login)):
                        raise TaskError("host_quote_invalid", "quote loader changed original attribution")
                    quotes.append(quote)
                forwards = await self.forward_loader(frozen_forwards, message_id=event_id, client=event_client, scope=scope,
                                                     intake=intake) if frozen_forwards else ()
                if (not isinstance(forwards, tuple) or len(forwards) != len(frozen_forwards)
                        or any(type(f) is not CapturedForward or (f.position, f.resource_hash) != (l.position, digest(l.resource))
                               for f, l in zip(forwards, frozen_forwards)) or not fits_forwards(forwards)):
                    raise TaskError("host_forward_invalid", "forward loader changed its original locator or coverage")
                all_forwards = (*forwards, *(f for q in quotes for f in getattr(q, "forwards", ())))
                if not fits_forwards(all_forwards):
                    raise TaskError("host_forward_limit", "direct and quoted forwards exceed the shared input budget")
                all_images = (*images, *(i for q in quotes for i in event_images(q)), *(i for f in forwards for n in f.nodes for i in n.images))
                all_audios = (*audios, *(i for q in quotes for i in event_audios(q)),
                              *(i for f in forwards for n in f.nodes for i in getattr(n, "audios", ())))
                all_videos = (*videos, *(i for q in quotes for i in event_videos(q)),
                              *(i for f in forwards for n in f.nodes for i in getattr(n, "videos", ())))
                # Positions are unique within each parent, while the budget is shared.
                if not fits_budget(tuple(i.descriptor for i in all_images)):
                    raise TaskError("host_image_limit", "direct, quoted and forwarded images exceed the shared input budget")
                audio_descriptors = tuple(i.descriptor if hasattr(i, "descriptor") else i for i in all_audios)
                video_descriptors = tuple(i.descriptor if hasattr(i, "descriptor") else i for i in all_videos)
                if not fits_audio_budget(audio_descriptors, tuple(i.descriptor for i in all_images)):
                    raise TaskError("host_audio_limit", "voice and visual attachments exceed the shared intake budget")
                direct_videos = []
                for locator in frozen_videos:
                    video = await self.video_loader(locator)
                    if type(video) is not CapturedVideo or video.descriptor.position != locator.position:
                        raise TaskError("host_video_invalid", "video loader changed the original component position")
                    direct_videos.append(video)
                    if not fits_video_budget((*video_descriptors, *(v.descriptor for v in direct_videos)),
                                             tuple(i.descriptor for i in all_images), audio_descriptors):
                        raise TaskError("host_video_limit", "video and other attachments exceed the shared intake budget")
                return images, tuple(quotes), forwards, tuple(audios), tuple(direct_videos)
            images, quotes, forwards, audios, videos = (), (), (), (), ()
            if frozen_images or frozen_quotes or frozen_forwards or frozen_audios or frozen_videos:
                try:
                    images, quotes, forwards, audios, videos = await asyncio.wait_for(prepare(), MEDIA_PREPARE_TIMEOUT)
                except asyncio.TimeoutError:
                    raise TaskError("host_media_timeout", "attachment preparation exceeded its bounded intake time") from None
            if frozen_files:
                file_text = await prepare_file_text(frozen_files, self.file_loader, file_preferences,
                    client=event_client, scope=scope, payload_budget=65536 - sum(map(len, text_parts)))
                text = "".join(file_text.get(position, part) for position, part in enumerate(text_parts))
            if images or quotes or forwards or audios or videos or frozen_files:
                # Network/media preparation is outside SQLite; recheck the role,
                # authenticated sender and client before admitting the captured input.
                fresh_cid, fresh_role, fresh_instruction = await self._role(account, sender)
                if fresh_role != role:
                    raise TaskError("host_persona_changed", "host role changed while preparing attachments")
                fresh_actor = require(self.identities.resolve_authenticated(principal, observed_at=self.now()))
                if (fresh_actor.subject, fresh_actor.binding_id, fresh_actor.binding_revision) != (
                        actor.subject, actor.binding_id, actor.binding_revision) or event_client is not self.client(scope):
                    raise TaskError("host_image_owner_changed", "original image sender or client changed")
            return Outcome.success(NativePrivateInput(event_id, principal, ConversationRoute(scope, user=actor.subject),
                AudienceRef(scope, (actor.subject,)), text, at, role, instruction, tuple(images), quotes, forwards, audios, videos))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except asyncio.TimeoutError:
            return Outcome.failed("host_capture_timeout", "native host capture did not complete in time")
        except Exception:
            # Host errors can contain credentials, message bodies or database paths.
            return Outcome.failed("host_capture_failed", "native host fields or persona lookup could not be verified")

    async def receive(self, host, event):
        host.application.require_running()
        captured = await self.capture(event)
        if not captured.is_success:
            return captured
        return host.receive(captured.unwrap())
