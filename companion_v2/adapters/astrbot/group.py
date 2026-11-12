"""Verified group text and original media, including per-member sessions."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.conversation.media.models import MAX_INPUT_IMAGES, fits_budget
from companion_v2.conversation.media.audio.models import CapturedAudio, MAX_INPUT_AUDIOS
from companion_v2.conversation.media.video.models import CapturedVideo, MAX_VIDEOS, fits_video_budget
from companion_v2.conversation.forwards.models import CapturedForward, fits_forwards
from companion_v2.conversation.quotes.models import CapturedQuote, event_images, event_audios, event_videos, message_number
from companion_v2.conversation.quotes.forward_models import CapturedQuoteMedia, CapturedForwardQuote, CapturedForwardQuoteMedia
from .native import (NativeAudioLocator, NativeConversationInput, NativePrivateBridge, NativeVideoLocator,
                     MEDIA_PREPARE_TIMEOUT, account_number, event_account_id, event_group_id,
                     event_peer_id, event_session_id, event_native_client, native_client_connected, native_id, native_user_subject_id,
                     route_id, supported_platform_name)
from .quotes import NativeQuoteLocator, image_locator
from .forwards import NativeForwardLocator, resource_id
from .group_quotes import mention_text
from .files import file_locator, prepare_file_text, MAX_FILES


@dataclass(frozen=True)
class GroupEventRoute:
    platform_id: str
    account_id: str
    sender_id: str
    group_id: str
    session_id: str
    event_id: str
    _platform_name: str = ""

    @property
    def route_key(self):
        return self.platform_id, self.account_id, self.group_id

    @property
    def platform_name(self):
        return self._platform_name


def _is_sticker_segment(part):
    """OneBot marks a sticker image with sub_type=1 (or a 表情 summary)."""
    if not isinstance(part, Mapping) or part.get("type") != "image":
        return False
    data = part.get("data")
    if not isinstance(data, Mapping):
        return False
    if str(data.get("sub_type", "")).strip() == "1":
        return True
    return "表情" in str(data.get("summary") or "")


def group_event_route(event):
    """Cross-check normalized fields against one supported authenticated event."""
    message, meta, session = event.message_obj, event.platform_meta, event.session
    if (getattr(message.type, "value", None) != "GroupMessage"
            or getattr(session.message_type, "value", None) != "GroupMessage"):
        raise TaskError("host_scene_unsupported", "group capture requires a native group event")
    if not supported_platform_name(meta.name):
        raise TaskError("host_route_mismatch", "the native group platform is unsupported")
    sender, login, group = event_peer_id(event), event_account_id(event), event_group_id(event)
    event_id = native_id(message.message_id)
    if meta.name == "aiocqhttp":
        raw = getattr(message, "raw_message", None)
        if (not isinstance(raw, Mapping) or raw.get("post_type") != "message" or raw.get("message_type") != "group"
                or raw.get("anonymous") is not None or raw.get("sub_type") == "anonymous"
                or not isinstance(raw.get("sender"), Mapping)
                or tuple(account_number(raw.get(key)) for key in ("self_id", "user_id", "group_id")) != (login, sender, group)
                or account_number(raw["sender"].get("user_id")) != sender or native_id(raw.get("message_id")) != event_id
                or sender == login):
            raise TaskError("host_group_identity_mismatch", "group sender and route must match the original OneBot message")
        valid_sessions = (group, sender + "_" + group)
    elif meta.name == "wechat_native":
        valid_sessions = (event_session_id(event),)
    else:
        # QQ Official group openids are not OneBot envelopes.  AstrBot's
        # adapter already verified the websocket payload before creating the
        # native event; the fields below are its normalized projection.
        valid_sessions = (group,)
    if (session.platform_id != meta.id or session.platform_name != meta.id
            or session.session_id not in valid_sessions
            or message.session_id != (session.session_id if meta.name == "wechat_native" else group)
            or event.unified_msg_origin != f"{meta.id}:GroupMessage:{session.session_id}"):
        raise TaskError("host_route_mismatch", "group fields do not identify one native conversation")
    return GroupEventRoute(meta.id, login, sender, group, session.session_id, event_id, meta.name)


class NativeGroupBridge(NativePrivateBridge):
    scope_kind = ScopeKind.GROUP

    def __init__(self, *args, group_targets, **kwargs):
        self.group_targets = {t.route_key: t for t in group_targets}
        super().__init__(*args, **kwargs)

    @staticmethod
    def _umo(platform_id, session_id):
        return f"{platform_id}:GroupMessage:{session_id}"

    async def _role(self, account, session_id):
        conversation_id, role, instruction = await self._role_for_umo(account, self._umo(account.platform_id, session_id))
        return self._role_conversation_id(conversation_id, role), role, instruction

    @staticmethod
    def _connection(client, account_id):
        clients = getattr(client, "_wsr_api_clients", None)
        if isinstance(clients, Mapping) and account_id in clients:
            return clients[account_id]
        return client

    def _connection_current(self, account, client, connection):
        return (client is self.platforms[account.platform_id][1]
                and self.account_connected(account, client)
                and self._connection(client, account.self_id) is connection)

    @staticmethod
    def _text_only_parts(event, route, *, require_text=False):
        """Read the authenticated text skeleton without requiring attachments.

        AstrBot may hand the plugin a message whose quote/image/forward lookup
        is no longer available.  The raw OneBot envelope still gives us the
        text and mention components, so keep that evidence and mark the
        unavailable component with a bounded placeholder.  This mirrors V1's
        early group observation path while retaining the strict route checks.
        """
        raw = getattr(event.message_obj, "raw_message", None)
        segments = raw.get("message") if isinstance(raw, Mapping) else None
        parts = segments if isinstance(segments, list) else getattr(event.message_obj, "message", ())
        if not isinstance(parts, (list, tuple)) or not 1 <= len(parts) <= 128:
            raise TaskError("host_group_media_unsupported", "group text components are unavailable")
        raw_kinds = {
            part.get("type") for part in segments
            if isinstance(part, Mapping) and isinstance(part.get("type"), str)
        } if isinstance(segments, list) else set()
        native_kinds = {
            getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
            for part in getattr(event.message_obj, "message", ())
        }
        # AstrBot can replace a voice/video with a Plain component after a
        # failed host lookup.  The raw envelope must still match a native
        # component before its placeholder is accepted; otherwise an
        # untrusted text rewrite could masquerade as the original media.
        native_for_raw = {"image": "Image", "reply": "Reply", "forward": "Forward",
                          "record": "Record", "video": "Video", "file": "File"}
        if any(kind not in {"text", "at", "face", "mface", *native_for_raw} for kind in raw_kinds):
            raise TaskError("host_group_media_unsupported", "group component type is unavailable")
        if any(native_for_raw[kind] not in native_kinds for kind in raw_kinds if kind in native_for_raw):
            raise TaskError("host_group_media_unsupported", "group media differs from its original component")
        # A real OneBot envelope can carry an image/voice/video without any
        # plain text.  Keep that bounded placeholder so the group still has a
        # readable turn when the optional attachment lookup is unavailable.
        # Synthetic/preprocessed component lists without their raw envelope
        # remain strict, as they cannot prove which native media was sent.
        media_only_allowed = (
            bool(raw_kinds) and raw_kinds.issubset(
                {"image", "record", "video", "file", "reply", "forward"})) or (
            segments is None and any(kind in {"Image", "Record", "Video", "File", "Reply", "Forward"}
                                     for kind in native_kinds))
        text, has_text = [], False
        for part in parts:
            if isinstance(part, Mapping):
                kind, data = part.get("type"), part.get("data")
                if not isinstance(data, Mapping):
                    raise TaskError("host_group_media_unsupported", "group component data is unavailable")
                if kind == "text" and isinstance(data.get("text"), str):
                    value = data["text"]
                    text.append(value)
                    has_text |= bool(value.strip())
                elif kind == "at":
                    try:
                        value = mention_text(data.get("qq"), route.account_id,
                                             bot_ids=(event.message_obj.self_id, "qq_official"))
                        text.append(value)
                        has_text |= value == "[提及机器人]"
                    except (TaskError, ValueError, TypeError):
                        text.append("[提及不可用]")
                elif kind == "image":
                    # A QQ sticker image (sub_type=1) is an emotional expression,
                    # not a photo to caption; keep it as the member's message so
                    # the turn does not depend on fetching the attachment.
                    if _is_sticker_segment(part):
                        text.append("[表情包]")
                        has_text = True
                    else:
                        text.append("\n[图片不可用]\n")
                elif kind == "reply":
                    text.append("\n[引用消息不可用]\n")
                elif kind == "forward":
                    text.append("\n[合并转发不可用]\n")
                elif kind == "record":
                    text.append("\n[语音不可用]\n")
                elif kind == "video":
                    text.append("\n[视频不可用]\n")
                elif kind == "file":
                    text.append("\n[文件附件，正文尚未读取]\n")
                elif kind in ("face", "mface"):
                    # A sticker carries a feeling, not an attachment. Count it as
                    # the member's message so the turn stays a normal reply.
                    text.append("[表情]")
                    has_text = True
                else:
                    text.append("\n[消息组件不可用]\n")
                continue
            kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
            if kind == "Plain" and isinstance(getattr(part, "text", None), str):
                value = part.text
                text.append(value)
                has_text |= bool(value.strip())
            elif kind == "At":
                try:
                    value = mention_text(part.qq, route.account_id,
                                         bot_ids=(event.message_obj.self_id, "qq_official"))
                    text.append(value)
                    has_text |= value == "[提及机器人]"
                except (TaskError, ValueError, TypeError):
                    text.append("[提及不可用]")
            elif kind in ("Face", "Mface"):
                text.append("[表情]")
                has_text = True
            elif kind in ("Image", "Reply", "Forward", "Record", "Video", "File"):
                has_text |= kind == "Reply" and not require_text
                text.append({"Image": "\n[图片不可用]\n", "Reply": "\n[引用消息不可用]\n",
                             "Forward": "\n[合并转发不可用]\n", "Record": "\n[语音不可用]\n",
                             "Video": "\n[视频不可用]\n",
                             "File": "\n[文件附件，正文尚未读取]\n"}[kind])
            else:
                text.append("\n[消息组件不可用]\n")
        value = "".join(text)
        if ((not has_text and (require_text or not media_only_allowed))
                or not value.strip() or len(value) > 65536):
            raise TaskError("host_text_invalid", "group input must be bounded nonempty text")
        return value

    async def capture_text_only(self, event):
        """Capture a group turn when optional native media is unavailable."""
        try:
            if not isinstance(event, self.event_type):
                raise TaskError("host_event_unsupported", "group capture requires the verified native event class")
            route = group_event_route(event)
            target = self.target_for_route(*route.route_key)
            account = self.accounts.get((route.platform_id, route.account_id))
            if target is None or account is None or target.bot_id != account.bot_id:
                raise TaskError("host_target_unavailable", "group and account require an explicit target mapping")
            parts = self.component_reader(event) if self.component_reader is not None else tuple(event.message_obj.message)
            raw = getattr(event.message_obj, "raw_message", None)
            original_parts = raw.get("message") if isinstance(raw, Mapping) else None
            self._validate_quote_components(original_parts, parts)
            text = self._text_only_parts(event, route)
            at, event_client = datetime.fromtimestamp(event.created_at, timezone.utc), event_native_client(
                self.context.get_platform_inst(route.platform_id), event)
            principal = AuthenticatedHostPrincipal(self.installation_id, self.host_id, route.platform_id,
                route.sender_id, account.bot_id, route.account_id)
            cid, role, instruction = await self._role(account, route.session_id)
            scope = ScopeRef(self.installation_id, route.platform_id, ScopeKind.GROUP, route.group_id,
                             account.bot_id, route.account_id, cid, route.session_id)
            if event_client is not self.client(scope) or not self.account_connected(account, event_client):
                raise TaskError("host_client_mismatch", "group event did not originate from the connected account client")
            speaker = SubjectRef(self.installation_id, SubjectKind.HUMAN,
                native_user_subject_id(route.platform_name, route.sender_id))
            return Outcome.success(NativeConversationInput(route.event_id, principal,
                ConversationRoute(scope, group_id=route.group_id), group_audience(scope, speaker), text, at, role,
                instruction))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("host_capture_failed", "group route, sender or persona could not be verified")

    @staticmethod
    def _validate_quote_components(original_parts, parts):
        if original_parts is None:
            return
        if (not isinstance(original_parts, list) or not 1 <= len(original_parts) <= 128
                or any(not isinstance(part, Mapping) or not isinstance(part.get("data"), Mapping)
                       or part.get("type") not in ("text", "at", "face", "mface", "image", "reply",
                                                   "forward", "record", "video", "file")
                       for part in original_parts)):
            raise TaskError("host_group_media_unsupported", "original group components need their own source adapter")
        try:
            # AstrBot may normalize or drop At components before plugin handlers,
            # so component positions are not stable across preprocessing.  The
            # quoted message identifiers and their order are the security-
            # relevant invariant for the text-only group capture.
            raw_quotes = tuple(message_number(part["data"].get("id"))
                               for part in original_parts if part.get("type") == "reply")
            native_quotes = tuple(message_number(part.id) for part in parts
                                  if getattr(getattr(part, "type", None), "value", getattr(part, "type", None)) == "Reply")
        except (TaskError, ValueError, TypeError, AttributeError):
            raise TaskError("host_quote_invalid", "group quote identifiers are not valid") from None
        if raw_quotes != native_quotes:
            raise TaskError("host_quote_invalid", "group quote component differs from its original OneBot segment")

    def target_for_route(self, platform_id, account_id, group_id):
        target = self.group_targets.get((platform_id, account_id, group_id))
        if target is None:
            template = self.group_targets.get((platform_id, account_id, "*"))
            if template is not None:
                target = replace(template, target_id="group-" + digest([platform_id, account_id, group_id])[:24],
                                 group_id=group_id)
        return target

    def target_for_scope(self, scope):
        target = self.target_for_route(scope.platform, scope.account_id, scope.native_scope_id)
        if scope.kind is not ScopeKind.GROUP or target is None or scope.bot_id != target.bot_id:
            raise TaskError("host_target_unavailable", "the native group target is not configured")
        return target

    def client(self, scope):
        self.target_for_scope(scope)
        return super().client(scope)

    def _session_matches_group(self, account, session_id, group_id):
        if self.platform_name(account) == "wechat_native":
            from .wechat_native import session_target
            try:
                return session_target(self.platforms[account.platform_id][0], session_id) == group_id
            except ValueError:
                return False
        if self.platform_name(account) == "aiocqhttp":
            parts = session_id.split("_")
            return (len(parts) in (1, 2)
                    and all(account_number(part) == part for part in parts)
                    and parts[-1] == group_id)
        return session_id == group_id

    def _verified_persona_umo(self, scope, binding, account):
        if (not self._session_matches_group(account, scope.session_id, scope.native_scope_id)
                or binding.scope != scope):
            raise TaskError("host_route_changed", "original group session is inconsistent")
        return self._umo(account.platform_id, scope.session_id)

    async def verify_role(self, scope, binding, instruction=None):
        self.client(scope)
        account = self.accounts[(scope.platform, scope.account_id)]
        if (not self._session_matches_group(account, scope.session_id, scope.native_scope_id)
                or binding.scope != scope):
            raise TaskError("host_route_changed", "original group session is inconsistent")
        cid, role, prompt = await self._role(account, scope.session_id)
        self.client(scope)
        if cid != scope.conversation_id or role != binding.role or (instruction is not None and prompt != instruction):
            raise TaskError("host_persona_changed", "group conversation/persona differs from the original turn")
        return prompt

    @staticmethod
    def _text_fallback_allowed(code):
        """Only attachment availability failures may keep the frozen text."""
        return code in {
            "host_media_timeout",
            "host_quote_unavailable", "host_quote_timeout", "host_quote_unsupported",
            "host_forward_unavailable", "host_forward_timeout", "host_forward_unsupported",
            "host_image_unavailable", "host_image_fetch_failed", "host_image_format_unsupported",
            "host_audio_unavailable", "host_audio_fetch_failed", "host_audio_decode_timeout",
            "host_video_unavailable", "host_video_fetch_failed", "host_video_decode_timeout",
        }

    async def capture(self, event, *, allow_text_fallback=False):
        intake = self.forward_intake
        fallback_input = None
        try:
            if not isinstance(event, self.event_type):
                raise TaskError("host_event_unsupported", "group capture requires the verified native event class")
            route = group_event_route(event)
            target = self.target_for_route(*route.route_key)
            account = self.accounts.get((route.platform_id, route.account_id))
            if target is None or account is None or target.bot_id != account.bot_id:
                raise TaskError("host_target_unavailable", "group and account require an explicit target mapping")
            raw_message = getattr(event.message_obj, "raw_message", None)
            original_parts = raw_message.get("message") if isinstance(raw_message, Mapping) else None
            onebot = route._platform_name == "aiocqhttp"
            if original_parts is not None and (not isinstance(original_parts, list)
                    or not 1 <= len(original_parts) <= 128
                    or any(not isinstance(p, Mapping) or not isinstance(p.get("data"), Mapping)
                           or p.get("type") not in ("text", "at", "face", "mface", "image", "reply",
                                                     "forward", "record", "video", "file")
                           for p in original_parts)):
                # Host STT can replace Record with Plain before this plugin runs.
                # Such text cannot bypass the original group media contract.
                raise TaskError("host_group_media_unsupported", "original group components need their own source adapter")
            parts = self.component_reader(event) if self.component_reader is not None else tuple(event.message_obj.message)
            if not parts or len(parts) > 128:
                raise TaskError("host_media_unsupported", "message components are empty or exceed the intake budget")
            self._validate_quote_components(original_parts, parts)
            text_parts, image_locators, quote_locators, forward_locators = [], [], [], []
            audio_locators, video_locators = [], []
            file_locators = []
            for position, part in enumerate(parts):
                kind = getattr(part.type, "value", part.type)
                if kind == "Plain" and isinstance(part.text, str):
                    text_parts.append(part.text)
                elif kind == "At":
                    text_parts.append(mention_text(part.qq, route.account_id,
                                                   bot_ids=(event.message_obj.self_id, "qq_official")))
                elif kind == "Image" and self.image_type is not None and type(part) is self.image_type:
                    image_locators.append(image_locator(position, part.file, part.url))
                    text_parts.append("\n[图片 " + str(len(image_locators)) + "]\n")
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
                        raise TaskError("host_audio_unavailable", "group voice has no original host attachment")
                    audio_locators.append(NativeAudioLocator(position, value))
                    text_parts.append("\n[语音 " + str(len(audio_locators)) + "]\n")
                elif kind == "Video" and self.video_type is not None and type(part) is self.video_type:
                    value = part.file if type(part.file) is str and part.file.startswith("base64://") else part.url or part.file
                    if type(value) is not str or not value:
                        raise TaskError("host_video_unavailable", "group video has no original host attachment")
                    video_locators.append(NativeVideoLocator(position, value))
                    text_parts.append("\n[视频 " + str(len(video_locators)) + "]\n")
                elif kind == "File" and self.file_type is not None and type(part) is self.file_type:
                    if len(file_locators) < MAX_FILES:
                        file_locators.append(file_locator(position, part))
                    text_parts.append("\n[文件附件，正文尚未读取]\n")
                elif kind in ("Face", "Mface"):
                    # Sticker/emoji: an emotional expression with no payload. It
                    # must not reject the whole group turn as unreadable media.
                    text_parts.append("[表情]")
                else:
                    raise TaskError("host_group_media_unsupported", "this group component needs its own source adapter")
            if original_parts is not None:
                raw_forwards = tuple((position, part["data"].get("id"))
                    for position, part in enumerate(original_parts) if part.get("type") == "forward")
                if raw_forwards and self.forward_type is None:
                    raise TaskError("host_group_media_unsupported", "group forward source adapter is not installed")
                try:
                    original_forwards = tuple((position, resource_id(identifier)) for position, identifier in raw_forwards)
                except TaskError:
                    raise TaskError("host_group_media_unsupported", "original group forward identifier is unavailable") from None
                if original_forwards != tuple((item.position, item.resource) for item in forward_locators):
                    raise TaskError("host_group_media_unsupported", "host-preprocessed forwards need their original native component")
                for kind, locators in (("record", audio_locators), ("video", video_locators)):
                    originals = []
                    for position, part in enumerate(original_parts):
                        if part["type"] != kind:
                            continue
                        data = part["data"]
                        value = data.get("file")
                        if not (type(value) is str and value.startswith("base64://")):
                            value = data.get("url") or value
                        originals.append((position, value))
                    if tuple(originals) != tuple((item.position, item.locator) for item in locators):
                        raise TaskError("host_group_media_unsupported", "group media differs from its original component")
            elif onebot and (audio_locators or video_locators):
                raise TaskError("host_group_media_unsupported", "group media requires original OneBot components")
            if (len(image_locators) > MAX_INPUT_IMAGES or len(quote_locators) > 1
                    or len(forward_locators) > intake.max_forwards or len(audio_locators) > MAX_INPUT_AUDIOS
                    or len(video_locators) > MAX_VIDEOS):
                raise TaskError("host_group_media_limit", "group attachments exceed the input budget")
            text = "".join(text_parts)
            if not text.strip() or len(text) > 65536:
                raise TaskError("host_text_invalid", "group input must be bounded nonempty text")
            try:
                fallback_text = (self._text_only_parts(event, route, require_text=True)
                                 if allow_text_fallback else None)
            except TaskError:
                fallback_text = None
            at, event_client = datetime.fromtimestamp(event.created_at, timezone.utc), event_native_client(
                self.context.get_platform_inst(route.platform_id), event)
            principal = AuthenticatedHostPrincipal(self.installation_id, self.host_id, route.platform_id,
                route.sender_id, account.bot_id, route.account_id)
            # All mutable native fields above are frozen before host I/O. Identity
            # enrollment happens only in the later atomic, authorized admission.
            cid, role, instruction = await self._role(account, route.session_id)
            scope = ScopeRef(self.installation_id, route.platform_id, ScopeKind.GROUP, route.group_id,
                             account.bot_id, route.account_id, cid, route.session_id)
            if event_client is not self.client(scope) or not self.account_connected(account, event_client):
                raise TaskError("host_client_mismatch", "group event did not originate from the connected account client")
            connection = self._connection(event_client, route.account_id)
            subject_id = native_user_subject_id(route._platform_name, route.sender_id)
            if fallback_text is not None:
                speaker = SubjectRef(self.installation_id, SubjectKind.HUMAN, subject_id)
                fallback_input = NativeConversationInput(route.event_id, principal,
                    ConversationRoute(scope, group_id=route.group_id), group_audience(scope, speaker),
                    fallback_text, at, role, instruction)
            frozen_images, frozen_quotes, frozen_forwards = (tuple(image_locators), tuple(quote_locators),
                                                               tuple(forward_locators))
            frozen_audios, frozen_videos = tuple(audio_locators), tuple(video_locators)
            frozen_files = tuple(file_locators)
            from companion_v2.conversation.media.understanding import settings as understanding_settings
            file_preferences = understanding_settings(self.file_settings(scope, role) if self.file_settings else {})

            async def prepare():
                images = await self._capture_images(frozen_images)
                quotes = []
                for locator in frozen_quotes:
                    if locator.message_id == route.event_id:
                        raise TaskError("host_quote_invalid", "a group input cannot quote itself")
                    quote = await self.quote_loader(locator, client=event_client, scope=scope,
                                                    intake=intake)
                    if (type(quote) not in (CapturedQuote, CapturedQuoteMedia, CapturedForwardQuote, CapturedForwardQuoteMedia)
                            or quote.position != locator.position
                            or quote.message_id != locator.message_id
                            or (quote.sent_at is not None and quote.sent_at > at.timestamp())):
                        raise TaskError("host_quote_invalid", "group quote lost its original attribution or time")
                    quotes.append(quote)
                forwards = ()
                if frozen_forwards:
                    if self.forward_loader is None or self.forward_type is None:
                        raise TaskError("host_group_media_unsupported", "group forward source adapter is unavailable")
                    retained = tuple(i.descriptor for i in images) + tuple(
                        i.descriptor for quote in quotes for i in event_images(quote))
                    forwards = await self.forward_loader(frozen_forwards, message_id=route.event_id,
                        sender_id=route.sender_id, client=event_client, scope=scope, retained=retained,
                        intake=intake)
                    if (not isinstance(forwards, tuple) or len(forwards) != len(frozen_forwards)
                            or any(type(f) is not CapturedForward or
                                (f.position, f.resource_hash) != (l.position, digest(l.resource))
                                for f, l in zip(forwards, frozen_forwards)) or not fits_forwards(forwards)):
                        raise TaskError("host_forward_invalid", "group forward loader changed the captured manifest")
                if not fits_forwards((*forwards, *(f for quote in quotes for f in getattr(quote, "forwards", ())))):
                    raise TaskError("host_forward_limit", "direct and quoted group forwards share one input budget")
                all_images = tuple(i.descriptor for i in images) + tuple(
                    i.descriptor for quote in quotes for i in event_images(quote)) + tuple(
                    i.descriptor for forward in forwards for node in forward.nodes for i in node.images)
                if not fits_budget(all_images):
                    raise TaskError("host_group_media_limit", "direct, quoted and forwarded group images share one input budget")
                audio_descriptors = [getattr(item, "descriptor", item) for quote in quotes for item in event_audios(quote)]
                audio_descriptors.extend(item.descriptor for forward in forwards for node in forward.nodes for item in node.audios)
                video_descriptors = [getattr(item, "descriptor", item) for quote in quotes for item in event_videos(quote)]
                video_descriptors.extend(item.descriptor for forward in forwards for node in forward.nodes for item in node.videos)
                if not fits_video_budget(tuple(video_descriptors), all_images, tuple(audio_descriptors)):
                    raise TaskError("host_group_media_limit", "group attachments exceed their shared input budget")
                audios = []
                for locator in frozen_audios:
                    audio = await self.audio_loader(locator)
                    if type(audio) is not CapturedAudio or audio.descriptor.position != locator.position:
                        raise TaskError("host_audio_invalid", "group audio loader changed the original component position")
                    audios.append(audio)
                    audio_descriptors.append(audio.descriptor)
                    if not fits_video_budget(tuple(video_descriptors), all_images, tuple(audio_descriptors)):
                        raise TaskError("host_group_media_limit", "group voice and visual attachments exceed the input budget")
                videos = []
                for locator in frozen_videos:
                    video = await self.video_loader(locator)
                    if type(video) is not CapturedVideo or video.descriptor.position != locator.position:
                        raise TaskError("host_video_invalid", "group video loader changed the original component position")
                    videos.append(video)
                    video_descriptors.append(video.descriptor)
                    if not fits_video_budget(tuple(video_descriptors), all_images, tuple(audio_descriptors)):
                        raise TaskError("host_group_media_limit", "group video and other attachments exceed the input budget")
                if not fits_video_budget(tuple(video_descriptors), all_images, tuple(audio_descriptors)):
                    raise TaskError("host_group_media_limit", "direct, quoted and forwarded media share one input budget")
                return images, tuple(quotes), forwards, tuple(audios), tuple(videos)

            images, quotes, forwards, audios, videos = (), (), (), (), ()
            if frozen_images or frozen_quotes or frozen_forwards or frozen_audios or frozen_videos:
                try:
                    images, quotes, forwards, audios, videos = await asyncio.wait_for(prepare(), MEDIA_PREPARE_TIMEOUT)
                except asyncio.TimeoutError:
                    raise TaskError("host_media_timeout", "group attachment preparation exceeded its intake time") from None
            if frozen_files:
                file_text = await prepare_file_text(frozen_files, self.file_loader, file_preferences,
                    client=event_client, scope=scope, payload_budget=65536 - sum(map(len, text_parts)))
                text = "".join(file_text.get(position, part) for position, part in enumerate(text_parts))
            if frozen_images or frozen_quotes or frozen_forwards or frozen_audios or frozen_videos or frozen_files:
                if await self._role(account, route.session_id) != (cid, role, instruction):
                    raise TaskError("host_persona_changed", "group role changed while preparing attachments")
                if (event_client is not self.client(scope)
                        or not self._connection_current(account, event_client, connection)):
                    raise TaskError("host_client_mismatch", "original group account connection changed")
            speaker = SubjectRef(self.installation_id, SubjectKind.HUMAN, subject_id)
            return Outcome.success(NativeConversationInput(route.event_id, principal,
                ConversationRoute(scope, group_id=route.group_id), group_audience(scope, speaker), text, at, role, instruction,
                images, quotes, forwards, audios, videos))
        except TaskError as error:
            if fallback_input is not None and self._text_fallback_allowed(error.code):
                try:
                    if await self._role(account, route.session_id) != (cid, role, instruction):
                        raise TaskError("host_persona_changed", "group role changed while preparing attachments")
                    if (event_client is not self.client(scope)
                            or not self._connection_current(account, event_client, connection)):
                        raise TaskError("host_client_mismatch", "original group account connection changed")
                    return Outcome.success(fallback_input)
                except TaskError as changed:
                    return Outcome.failed(changed.code, str(changed))
                except Exception:
                    return Outcome.failed("host_capture_failed", "original group route could not be reverified")
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("host_capture_failed", "group route, sender or persona could not be verified")
