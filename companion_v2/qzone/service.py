"""The Zone operations themselves: scan, like, comment and publish.

Every public action follows the same order, because each step exists to prevent a
different failure: configuration and cooldown are checked *before* anything is
spent, the post handle must come from this very conversation, the local operation
is reserved before the request so a repeat cannot fire twice, and a timed-out POST
is recorded as unknown instead of being retried.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

from companion_v2.capabilities.models import CapabilityDesiredState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.qzone import feed as feed_reader
from companion_v2.qzone import protocol as P
from companion_v2.qzone.capability import (COMMENT_ACTION, EXTENSION, LIKE_ACTION, MAX_REFERENCES_PER_SCOPE,
    PUBLISH_ACTION, READ_ACTION, REFERENCE_TTL_MINUTES, package_descriptor)
from companion_v2.qzone.policy import (QzoneRuntimeStore, action_reason, block_reason, clear_failure,
    note_action, note_failure, note_publish, publish_reason)
from companion_v2.qzone.references import COMMENT, LIKE, QzoneReferenceStore
from companion_v2.qzone.agenda_link import (APPLIED, DISMISSED, MAX_PENDING, SuggestionStore)
from companion_v2.qzone.topics import (AgendaProposal, QzoneScene, QzoneTopic, normalize_scene,
    normalize_topic, topic_spec, validate_confidence)
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository


OWNER = "qzone.actions"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
VERIFY_DELAYS = (0.0, 0.45, 1.2)
PUBLISH_VERIFY_DELAYS = (0.0, 0.8, 1.8)
READ_TIMEOUT = 20.0
WRITE_TIMEOUT = 30.0
MAX_SCAN = 30
MAX_SOURCE_REFS = 32
MAX_SOURCE_REF_CHARS = 256
MAX_AGENDA_ID_CHARS = 160
MAX_AGENDA_INTENT_CHARS = 1000


def owner_key(context):
    """One Zone account per role; a window never owns its own public identity."""
    return "qzone-role:" + digest([context.scope.installation_id, context.role_binding.role.subject_id,
                                   context.scope.bot_id or ""])


def local_day(at):
    return at.astimezone(LOCAL_TIMEZONE).date().isoformat()


def _compact(text):
    return "".join(str(text or "").split())


def _observed_receipt(context, action, reference):
    """Stable receipt for an already verified remote state (no new request)."""
    operation_id = "qzone:observed:" + digest([context.key, action, reference])
    return operation_id, operation_id


def _source_refs(value):
    """Normalize source handles without inspecting or classifying their text."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_SOURCE_REFS:
        raise TaskError("qzone_request_invalid", "来源引用必须是有限的字符串列表")
    refs = tuple(item.strip() if isinstance(item, str) else "" for item in value)
    if any(not item or len(item) > MAX_SOURCE_REF_CHARS for item in refs):
        raise TaskError("qzone_request_invalid", "来源引用的格式或长度无效")
    return refs


def _agenda_proposal(value, *, topic, scene, source_refs):
    """Validate a pending agenda suggestion; no agenda repository is touched."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TaskError("qzone_request_invalid", "日程提案必须是对象")
    try:
        agenda_id = value.get("agenda_id", value.get("agenda_ref"))
        revision = value.get("expected_revision")
        intent = value.get("intent")
        refs = _source_refs(value.get("source_refs", source_refs))
        status = value.get("status", "pending_confirmation")
        if (type(agenda_id) is not str or not agenda_id.strip() or len(agenda_id.strip()) > MAX_AGENDA_ID_CHARS
                or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
                or type(intent) is not str or not intent.strip() or len(intent.strip()) > MAX_AGENDA_INTENT_CHARS
                or status != "pending_confirmation"):
            raise ValueError
        return AgendaProposal(agenda_id.strip(), revision, intent.strip(), refs, status)
    except (TypeError, ValueError):
        raise TaskError("qzone_request_invalid", "日程提案需要有效的标识、版本、意图和待确认状态") from None


class QzoneService:
    """Configured, throttled and receipted access to one role's Zone."""

    def __init__(self, runtime, settings, transport, *, now=None, references=None,
                 verify_delays=VERIFY_DELAYS, publish_verify_delays=PUBLISH_VERIFY_DELAYS):
        self.runtime = runtime
        self.settings = settings
        self.transport = transport
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.references = references or QzoneReferenceStore(ttl_minutes=REFERENCE_TTL_MINUTES,
                                                            capacity=MAX_REFERENCES_PER_SCOPE)
        self.store = QzoneRuntimeStore(runtime)
        self.operations = OperationRepository(runtime)
        self.capabilities = CapabilityRepository(runtime)
        # Pending agenda suggestions: recorded here, written back by the agenda domain.
        self.suggestions = SuggestionStore(runtime)
        self.package = package_descriptor()
        self.verify_delays = tuple(verify_delays)
        self.publish_verify_delays = tuple(publish_verify_delays)

    # ---- shared gates -------------------------------------------------

    def configuration(self, context, at):
        return self.settings.read(context, at)

    def state(self, context):
        return self.store.read(owner_key(context))

    def last_credential_state(self):
        """The transport's latest page-facing credential conclusion, read now.

        The host transport owns the session and re-binds this conclusion on every
        acquisition, so a copy taken when a route was built goes stale the moment
        that same request refreshes the session. Reading it here -- and never
        probing the network -- keeps the page row honest without turning
        ``/status`` into a blocking credential lookup. A transport that cannot
        report a conclusion contributes an empty row rather than a made-up state.
        """
        state = getattr(self.transport, "last_credential_state", None)
        return dict(state or {})

    def agenda_suggestions(self, context, *, limit=MAX_PENDING):
        """Pending suggestions this role is still waiting on an answer for."""
        return self.suggestions.pending(owner_key(context), limit=limit)

    def all_agenda_suggestions(self, context, *, limit=20):
        return self.suggestions.list(owner_key(context), limit=limit)

    def _drop_suggestion(self, context, operation, proposal, *, reason="publish_failed"):
        """Cleanup on a known failure; a failure here must not mask the original error."""
        try:
            owner = owner_key(context)
            suggestion_id = SuggestionStore.suggestion_id(owner, operation.operation_id, proposal.agenda_id)
            self.suggestions.resolve(owner, suggestion_id, state=DISMISSED, at=self.now(),
                                     result_refs=(reason,))
        except (TaskError, ValueError):
            pass

    async def resolve_agenda_suggestion(self, context, *, suggestion_id, state, operation_key,
                                        calendar_operation_id="", result_refs=()):
        """Close one suggestion: either a calendar receipt proves the write-back, or it is dropped.

        The agenda change itself is not performed here -- that belongs to the
        calendar domain, which owns the evidence checks for its own records. This
        call only records which operation closed the question, with the same
        reserve/transition discipline every other Zone action uses.
        """
        at = self.now()
        normalized = str(state or "").strip()
        if normalized not in (APPLIED, DISMISSED):
            raise TaskError("qzone_suggestion_state_invalid", "建议只能标记为已写回或已忽略")
        refs = _source_refs(result_refs)
        operation, replayed = self._reserve(context, action="qzone.agenda.suggestion",
                                            operation_key=operation_key,
                                            parameters={"suggestion_id": str(suggestion_id),
                                                        "state": normalized,
                                                        "calendar_operation_id": str(calendar_operation_id or ""),
                                                        "result_refs": list(refs)}, at=at)
        if replayed:
            _code, receipt = self._suggestion_receipt(context, operation.operation_id)
            if receipt is None:
                raise TaskError("qzone_operation_unresolved",
                                "上一次同类操作结果未知；请确认后再用新的操作重试")
            return {"status": "replayed", "suggestion": receipt.summary()}
        closed = self.suggestions.resolve(owner_key(context), str(suggestion_id), state=normalized,
                                         calendar_operation_id=str(calendar_operation_id or ""),
                                         result_refs=refs, at=at)
        if closed.state != normalized:
            # Already closed the other way: a dismissal cannot be turned into an
            # applied write-back (or back again) under a fresh operation key.
            raise TaskError("qzone_suggestion_state_invalid",
                            "这条日程建议已经处理过了（已写回或已忽略），不能再次改判")
        self._remember_suggestion_receipt(context, operation.operation_id, closed, at=at)
        self._complete(operation, at)
        return {"status": "applied" if normalized == APPLIED else "dismissed",
                "suggestion": closed.summary()}

    def _suggestion_receipt(self, context, operation_id):
        row = self.runtime.read_one("""SELECT document_json FROM memory_views
            WHERE view_id = ? AND kind = 'qzone.agenda_resolution.v1'""",
            ("qzone-agenda-resolution:" + digest([operation_id])[:32],))
        if row is None:
            return None, None
        try:
            payload = json.loads(row["document_json"])
        except (TypeError, ValueError):
            return None, None
        suggestion = self.suggestions.read(owner_key(context), str(payload.get("suggestion_id") or ""))
        return payload, suggestion

    def _remember_suggestion_receipt(self, context, operation_id, suggestion, *, at):
        view_id = "qzone-agenda-resolution:" + digest([operation_id])[:32]
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT revision FROM memory_views WHERE view_id = ?", (view_id,)).fetchone()
            revision = int(row["revision"]) + 1 if row is not None else 1
            connection.execute("""INSERT INTO memory_views VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(view_id) DO UPDATE SET revision = excluded.revision,
                    document_json = excluded.document_json, updated_at = excluded.updated_at""",
                (view_id, owner_key(context), "qzone.agenda_resolution.v1", revision,
                 json.dumps({"suggestion_id": suggestion.suggestion_id, "state": suggestion.state},
                            ensure_ascii=False, sort_keys=True), encode({"operation_id": operation_id}), stamp(at)))

    def status(self, context, at):
        """What the page and the model need to know, without touching the network."""
        state = self.state(context)
        try:
            settings = self.configuration(context, at)
        except (TaskError, ValueError) as error:
            settings = {}
            settings_error = str(error)
        else:
            settings_error = ""
        return {"settings": settings, "settings_error": settings_error,
                "blocked_reason": block_reason(state, at=at), "auth_status": state.last_auth_status,
                # The one source of truth for the page's credential row: read from
                # the transport at call time, so a refresh-and-report response can
                # never carry the conclusion from before that refresh.
                "credential": self.last_credential_state(),
                "publish_day": state.publish_day, "publish_count": state.publish_count,
                "recent_publish_texts": list(state.recent_publish_texts),
                # Open questions this role is waiting on; written back through the
                # agenda domain, never here.
                "agenda_suggestions": [item.summary() for item in self.agenda_suggestions(context)]}

    async def _credential(self, context, at):
        try:
            return await self.transport.credential()
        except TaskError as error:
            recoverable = P.missing_credential_reason(str(error)) or error.code == "qzone_credential_missing"
            state = note_failure(self.state(context), reason=str(error), source="credential", at=at,
                                 recoverable=recoverable)
            self.store.write(owner_key(context), state, at=at)
            raise

    def _settings_gate(self, context, at):
        settings = self.configuration(context, at)
        if not settings.get("enabled"):
            raise TaskError("qzone_disabled", "QQ 空间能力未开启")
        blocked = block_reason(self.state(context), at=at)
        if blocked:
            raise TaskError("qzone_auth_blocked", blocked)
        return settings

    def _require_capability(self, context, capability):
        """Installation, role switch and this conversation's consent, all at once.

        Loading the plugin proves none of them: an operator installs the extension,
        enables it for the role and consents inside this window, and every action
        re-checks all three so a later revocation takes effect immediately.
        """
        installed = self.capabilities.installation(extension_id=EXTENSION)
        selected = installed.unwrap() if installed.is_success else None
        if selected is None:
            raise TaskError("qzone_not_installed", "QQ 空间拓展尚未安装")
        if (selected.package_id, selected.package_version, selected.artifact_hash) != (
                self.package.package_id, self.package.package_version, self.package.artifact_hash):
            raise TaskError("qzone_package_changed", "已安装的 QQ 空间拓展与当前版本不一致")
        if selected.desired_state is not CapabilityDesiredState.ACTIVE:
            raise TaskError("qzone_not_active", "QQ 空间拓展已暂停")
        require(self.capabilities.require_role_enabled(extension_id=EXTENSION, role=context.role_binding.role,
                                                      bot_id=context.scope.bot_id))
        require(self.capabilities.authorize(extension_id=EXTENSION, capability_id=capability,
                                           subject=context.actor.subject, scope=context.scope))

    def _success(self, context, at):
        state = clear_failure(self.state(context))
        self.store.write(owner_key(context), state, at=at)

    def _failure(self, context, at, *, reason, source):
        state = note_failure(self.state(context), reason=reason, source=source, at=at)
        self.store.write(owner_key(context), state, at=at)

    def _answer(self, outcome, context, at, *, source):
        """Turn one transport answer into a payload, or into an explicit refusal."""
        if outcome.delivery_unknown:
            raise TaskError("qzone_delivery_unknown", "QQ 空间的回复没有到达，结果未知；不会自动重发")
        if not outcome.ok or not outcome.text.strip():
            reason = outcome.error or f"QQ 空间返回 HTTP {outcome.status}"
            if P.looks_like_auth_failure(reason):
                self._failure(context, at, reason=reason, source=source)
                raise TaskError("qzone_auth_blocked", f"{reason}（已暂停，请刷新凭据）")
            raise TaskError("qzone_operation_failed", reason[:200])
        payload = feed_reader.decode_payload(outcome.text)
        code = P.response_code(payload)
        if code not in (0, "0"):
            message = str(payload.get("message") or payload.get("msg") or code)[:200]
            if P.looks_like_auth_failure(message):
                self._failure(context, at, reason=message, source=source)
                raise TaskError("qzone_auth_blocked", f"{message}（已暂停，请刷新凭据）")
            raise TaskError("qzone_operation_failed", message)
        return payload

    async def _recent_posts(self, credential, *, uin, num, at, context, source):
        outcome = await self.transport.send(P.feed_request(credential, uin=uin, num=num), timeout=READ_TIMEOUT)
        payload = self._answer(outcome, context, at, source=source)
        return feed_reader.posts_from_msglist(payload, my_uin=credential.uin, limit=num)

    def _reserve(self, context, *, action, operation_key, parameters, at):
        key = digest([context.key, action, operation_key])
        operation = OperationRef("qzone:" + key, OWNER, context.scope, VersionRef(key, 1), key)
        receipt = require(self.operations.reserve(operation, parameter_hash=digest(parameters), observed_at=at))
        if receipt.replayed:
            if receipt.state is not OperationState.COMPLETED:
                raise TaskError("qzone_operation_unresolved",
                                "上一次同类操作结果未知；请确认空间里的实际结果，再用新的操作重试")
            return operation, True
        return operation, False

    def _complete(self, operation, at):
        require(self.operations.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                                          next_state=OperationState.COMPLETED, observed_at=at))

    def _unknown(self, operation, at):
        require(self.operations.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                                          next_state=OperationState.UNKNOWN, observed_at=at))

    # ---- operations ---------------------------------------------------

    async def scan(self, context, *, scope="friends", limit=None, detail=False):
        """Read the Zone: one person's timeline or the friend timeline, bounded."""
        at = self.now()
        settings = self._settings_gate(context, at)
        self._require_capability(context, READ_ACTION)
        credential = await self._credential(context, at)
        bound = max(1, min(int(limit or settings.get("feed_recent_posts") or 10), MAX_SCAN))
        if scope == "own":
            posts = await self._recent_posts(credential, uin=credential.uin, num=bound, at=at,
                                             context=context, source="feed")
        elif scope == "friends":
            posts = await self._friends(credential, bound, at=at, context=context)
        else:
            raise TaskError("qzone_scope_invalid", "刷空间只支持自己的动态或好友动态")
        references = self.references.issue(posts, owner_key=owner_key(context), scope_key=context.key, at=at)
        self._success(context, at)
        items = [reference.summary() for reference in references]
        if detail and posts:
            items = await self._items_with_comments(credential, posts, references, bound, at, context)
        return {"scope": scope, "count": len(items), "items": items}

    async def _friends(self, credential, limit, *, at, context):
        """The H5 index carries the tickets; the legacy list covers what it misses."""
        posts = ()
        index = await self.transport.send(P.h5_index_request(credential), timeout=READ_TIMEOUT)
        if index.ok and index.text.strip():
            posts = feed_reader.posts_from_html(index.text, my_uin=credential.uin, limit=limit)
            token = P.qzonetoken_from_index(index.text)
            if token and token != credential.qzonetoken:
                credential = P.QzoneCredential(uin=credential.uin, secret=credential.secret, g_tk=credential.g_tk,
                                               qzonetoken=token, header=credential.header, source=credential.source)
        if len(posts) >= limit:
            return posts
        outcome = await self.transport.send(P.friends_feed_request(credential, page=1, count=max(10, limit)),
                                            timeout=READ_TIMEOUT)
        if outcome.ok and outcome.text.strip():
            try:
                more = feed_reader.posts_from_legacy_feed(feed_reader.decode_payload(outcome.text),
                                                          my_uin=credential.uin, limit=limit)
            except TaskError:
                more = ()
            seen = {(post.author_uin, post.post_id) for post in posts}
            posts = (*posts, *(post for post in more if (post.author_uin, post.post_id) not in seen))
        return posts[:limit]

    async def _items_with_comments(self, credential, posts, references, limit, at, context):
        """Comments of the scanned posts, read through the detail form of the list call."""
        by_id = {post.post_id: reference for post, reference in zip(posts, references)}
        items = [reference.summary() for reference in references]
        targets = [post for post in posts if post.own][:3] or list(posts[:1])
        for post in targets:
            outcome = await self.transport.send(P.feed_request(credential, uin=int(post.author_uin), num=limit,
                                                              detail=True), timeout=READ_TIMEOUT)
            payload = self._answer(outcome, context, at, source="feed.detail")
            entry = feed_reader.msglist_entry(payload, post.post_id)
            if entry is None:
                continue
            comments = feed_reader.comments_from_mapping(entry)
            for item in items:
                if item["post_ref"] == by_id[post.post_id].token:
                    item["comments"] = list(comments)
        return items

    async def like(self, context, *, post_ref, operation_key):
        at = self.now()
        settings = self._settings_gate(context, at)
        self._require_capability(context, LIKE_ACTION)
        reason = action_reason(self.state(context), settings, kind=LIKE, at=at, day=local_day(at))
        if reason:
            raise TaskError("qzone_action_blocked", reason)
        reference = self.references.resolve(post_ref, owner_key=owner_key(context), scope_key=context.key,
                                            at=at, kind=LIKE)
        if reference.post.liked:
            operation_id, receipt_ref = _observed_receipt(context, LIKE_ACTION, post_ref)
            return {"status": "already", "verified": True, "message": "这条动态此前已经点过赞",
                    "operation_id": operation_id, "receipt_ref": receipt_ref}
        credential = await self._credential(context, at)
        operation, replayed = self._reserve(context, action=LIKE_ACTION, operation_key=operation_key,
                                            parameters={"post_ref": post_ref}, at=at)
        if replayed:
            return {"status": "replayed", "verified": True, "message": "这次点赞此前已经确认完成",
                    "operation_id": operation.operation_id, "receipt_ref": operation.operation_id}
        outcome = await self.transport.send(P.like_request(credential, reference.post), timeout=WRITE_TIMEOUT)
        if outcome.delivery_unknown:
            self._unknown(operation, at)
            raise TaskError("qzone_delivery_unknown", "点赞请求的结果未知；不会自动重发，请刷新空间确认")
        self._answer(outcome, context, at, source="post.like")
        verified = await self._verify_like(credential, reference.post, at=at, context=context)
        self._complete(operation, at)
        self.references.mark_used(reference, LIKE)
        if verified:
            self.references.mark_liked(reference)
        self.store.write(owner_key(context), note_action(self.state(context), kind=LIKE, at=at), at=at)
        return {"status": "done" if verified else "accepted", "verified": verified,
                "message": "点赞已确认" if verified else "点赞请求已受理，等待 QQ 空间同步",
                "operation_id": operation.operation_id, "receipt_ref": operation.operation_id}

    async def _verify_like(self, credential, post, *, at, context):
        for delay in self.verify_delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                posts = await self._recent_posts(credential, uin=int(post.author_uin), num=10, at=at,
                                                 context=context, source="post.like.verify")
            except TaskError:
                return False
            for candidate in posts:
                if candidate.post_id == post.post_id or (post.fid and candidate.post_id == post.fid):
                    return bool(candidate.liked)
        return False

    async def comment(self, context, *, post_ref, text, operation_key):
        at = self.now()
        settings = self._settings_gate(context, at)
        self._require_capability(context, COMMENT_ACTION)
        reason = action_reason(self.state(context), settings, kind=COMMENT, at=at, day=local_day(at))
        if reason:
            raise TaskError("qzone_action_blocked", reason)
        body = P.clean_text(text, limit=P.MAX_COMMENT_CHARS, field="回复正文")
        reference = self.references.resolve(post_ref, owner_key=owner_key(context), scope_key=context.key,
                                            at=at, kind=COMMENT)
        credential = await self._credential(context, at)
        operation, replayed = self._reserve(context, action=COMMENT_ACTION, operation_key=operation_key,
                                            parameters={"post_ref": post_ref, "text": body}, at=at)
        if replayed:
            return {"status": "replayed", "message": "这次回复此前已经确认完成",
                    "operation_id": operation.operation_id, "receipt_ref": operation.operation_id}
        outcome = await self.transport.send(P.comment_request(credential, reference.post, body),
                                            timeout=WRITE_TIMEOUT)
        if outcome.delivery_unknown:
            self._unknown(operation, at)
            raise TaskError("qzone_delivery_unknown", "回复的结果未知；不会自动重发，请刷新空间确认")
        self._answer(outcome, context, at, source="post.comment")
        self._complete(operation, at)
        self.references.mark_used(reference, COMMENT)
        self.store.write(owner_key(context), note_action(self.state(context), kind=COMMENT, at=at), at=at)
        return {"status": "done", "message": "回复已发布", "post_ref": post_ref,
                "operation_id": operation.operation_id, "receipt_ref": operation.operation_id}

    async def publish(self, context, *, text, topic, scene, confidence, topic_confirmed, operation_key,
                      source_refs=(), agenda_proposal=None):
        """Publish only after a source-guided topic decision has been attached.

        Classification is deliberately a contract boundary rather than a local
        text classifier.  Callers may use a model, a page or another trusted
        adapter to choose the closed topic vocabulary; this service only checks
        the decision, configured gates and the resulting public operation.
        """
        at = self.now()
        settings = self._settings_gate(context, at)
        self._require_capability(context, PUBLISH_ACTION)
        body = P.clean_text(text, limit=P.MAX_PUBLISH_CHARS, field="说说正文")
        refs = _source_refs(source_refs)
        code, message = publish_topic_reason = (None, None)
        try:
            normalized_topic = normalize_topic(topic)
        except ValueError:
            raise TaskError("qzone_topic_invalid", "主题不在可用目录中") from None
        try:
            normalized_scene = normalize_scene(scene)
        except ValueError:
            raise TaskError("qzone_scene_invalid", "发送场景不在可用目录中") from None
        try:
            normalized_confidence = validate_confidence(confidence)
        except ValueError:
            raise TaskError("qzone_topic_confidence_invalid", "主题判断置信度必须是 0 到 1 之间的数字") from None
        if normalized_scene not in topic_spec(normalized_topic).scenes:
            raise TaskError("qzone_scene_invalid", "这个场景不适合所选主题")
        proposal = _agenda_proposal(agenda_proposal, topic=normalized_topic, scene=normalized_scene,
                                    source_refs=refs)
        if proposal is not None and (normalized_topic is not QzoneTopic.HELP_INTERACTION
                                     or normalized_scene is not QzoneScene.AGENDA):
            raise TaskError("qzone_request_invalid", "日程提案只能随日程场景的求助互动提交")
        from companion_v2.qzone.policy import topic_reason
        code, message = topic_reason(self.state(context), settings, topic=normalized_topic,
                                      scene=normalized_scene, confidence=normalized_confidence,
                                      topic_confirmed=topic_confirmed, at=at, day=local_day(at))
        if code:
            raise TaskError(code, message)
        state = self.state(context)
        reason = publish_reason(state, settings, text=body, at=at, day=local_day(at),
                                topic=normalized_topic, scene=normalized_scene,
                                confidence=normalized_confidence, topic_confirmed=topic_confirmed)
        if reason:
            raise TaskError("qzone_action_blocked", reason)
        credential = await self._credential(context, at)
        parameters = {"text": body, "topic": normalized_topic.value, "scene": normalized_scene.value,
                      "confidence": normalized_confidence, "topic_confirmed": topic_confirmed,
                      "source_refs": list(refs),
                      "agenda_proposal": proposal.document() if proposal else None}
        operation, replayed = self._reserve(context, action=PUBLISH_ACTION, operation_key=operation_key,
                                            parameters=parameters, at=at)
        if replayed is False and proposal is not None:
            # Recorded before the request, once per original operation: a replay or
            # a crash cannot create a second pending suggestion, and a post whose
            # result is unknown still leaves the question visible to its owner.
            self.suggestions.record(owner_key=owner_key(context), operation_id=operation.operation_id,
                                    agenda_id=proposal.agenda_id, expected_revision=proposal.expected_revision,
                                    intent=proposal.intent, source_refs=proposal.source_refs,
                                    topic=normalized_topic.value, scene=normalized_scene.value, at=at)
        if replayed:
            result = {"status": "replayed", "verified": True, "message": "这条说说此前已经确认发布",
                      "operation_id": operation.operation_id, "receipt_ref": operation.operation_id,
                      "topic": normalized_topic.value, "scene": normalized_scene.value,
                      "confidence": normalized_confidence, "topic_confirmed": topic_confirmed,
                      "source_refs": list(refs)}
            if proposal:
                result["agenda_proposal"] = proposal.document()
            return result
        outcome = await self.transport.send(P.publish_request(credential, body), timeout=WRITE_TIMEOUT)
        if outcome.delivery_unknown:
            self._unknown(operation, at)
            raise TaskError("qzone_delivery_unknown", "说说是否发出未知；不会自动重发，请到空间确认后再决定")
        try:
            self._answer(outcome, context, at, source="post.publish")
        except TaskError as error:
            # A known failure means the question was never asked in public, so the
            # suggestion must not stay open. An unknown result keeps it: the post
            # may well be out there.
            if proposal is not None and error.code != "qzone_delivery_unknown":
                self._drop_suggestion(context, operation, proposal)
            raise
        verified = await self._verify_publish(credential, body, at=at, context=context)
        self._complete(operation, at)
        updated = note_action(state, kind="publish", at=at)
        self.store.write(owner_key(context), note_publish(updated, text=body, at=at, day=local_day(at),
                                                          topic=normalized_topic), at=at)
        result = {"status": "done" if verified else "accepted", "verified": verified,
                  "message": "说说已发布" if verified else "说说已提交，等待 QQ 空间同步",
                  "operation_id": operation.operation_id, "receipt_ref": operation.operation_id,
                  "topic": normalized_topic.value, "scene": normalized_scene.value,
                  "confidence": normalized_confidence, "topic_confirmed": topic_confirmed,
                  "source_refs": list(refs)}
        if proposal:
            result["agenda_proposal"] = proposal.document()
            result["message"] += "；日程提案仍待确认"
        return result

    async def _verify_publish(self, credential, text, *, at, context):
        needle = _compact(text)
        for delay in self.publish_verify_delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                posts = await self._recent_posts(credential, uin=credential.uin, num=5, at=at,
                                                 context=context, source="post.publish.verify")
            except TaskError:
                return False
            for post in posts:
                if needle and needle in _compact(post.excerpt):
                    return True
        return False


__all__ = ["LOCAL_TIMEZONE", "OWNER", "QzoneService", "local_day", "owner_key"]
