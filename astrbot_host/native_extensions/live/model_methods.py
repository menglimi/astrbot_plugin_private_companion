"""V2 live model services."""
from __future__ import annotations
from .common import *
from .native_context import live_private_persona_prompt
from ..usage import tracked_text_chat

class LiveModelMethods:

    def _should_collect_for_auto_reply(self, event: LiveDanmakuEvent) -> bool:
        if not self.config.get('bili_live_auto_reply_enabled', False):
            return False
        if self.config.get('bili_live_auto_reply_skip_emoticon_danmaku', True) and (event.is_emoticon_danmaku or event.is_voice_danmaku):
            return False
        if event.event_type in self._bili_guaranteed_support_types():
            if event.event_id in self._bili_acknowledged_support_event_ids:
                return False
            if event.event_id in self._bili_processing_support_event_ids:
                return False
            if any((item.event_id == event.event_id for item in self._bili_pending_reply_events)):
                return False
            return True
        event_types = self.config.get('bili_live_auto_reply_event_types', ['danmaku'])
        if not isinstance(event_types, list):
            event_types = ['danmaku']
        return event.event_type in {str(item).strip() for item in event_types}

    def _schedule_bili_auto_reply(self) -> None:
        if self._bili_auto_reply_task and (not self._bili_auto_reply_task.done()):
            return
        self._bili_auto_reply_task = asyncio.create_task(self._bili_auto_reply_worker())

    async def _bili_auto_reply_worker(self) -> None:
        events: list[LiveDanmakuEvent] = []
        processing_support_ids: set[str] = set()
        batch_drained = False
        try:
            cooldown = max(1.0, self._safe_parse_float(self.config.get('bili_live_auto_reply_cooldown_seconds'), 12.0))
            elapsed = time.time() - self._bili_last_auto_reply_at
            remaining = max(0.0, cooldown - elapsed)
            while remaining > 0 and (not any((item.event_type in self._bili_guaranteed_support_types() for item in self._bili_pending_reply_events))):
                await asyncio.sleep(min(0.25, remaining))
                remaining = max(0.0, cooldown - (time.time() - self._bili_last_auto_reply_at))
            min_events = max(1, self._safe_parse_int(self.config.get('bili_live_auto_reply_min_events'), 1))
            has_guaranteed = any((item.event_type in self._bili_guaranteed_support_types() for item in self._bili_pending_reply_events))
            if len(self._bili_pending_reply_events) < min_events and (not has_guaranteed):
                return
            events = list(self._bili_pending_reply_events)
            processing_support_ids = {item.event_id for item in events if item.event_id and item.event_type in self._bili_guaranteed_support_types()}
            self._bili_processing_support_event_ids.update(processing_support_ids)
            self._bili_pending_reply_events.clear()
            batch_drained = True
            await self._reply_to_bili_live_events(events)
            await self._send_unacknowledged_bili_support_fallback(events)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f'[B站直播] 自动回应弹幕失败: {e}')
            if events:
                await self._send_unacknowledged_bili_support_fallback(events, reason=str(e))
        finally:
            self._bili_processing_support_event_ids.difference_update(processing_support_ids)
            current_task = asyncio.current_task()
            is_cancelling = bool(current_task and callable(getattr(current_task, 'cancelling', None)) and current_task.cancelling())
            if batch_drained and self._bili_pending_reply_events and (not is_cancelling):
                asyncio.get_running_loop().call_soon(self._schedule_bili_auto_reply)

    async def _get_bili_reply_session(self) -> str:
        configured = str(self.config.get('bili_live_auto_reply_session_id') or '').strip()
        if configured:
            self._live_bound_reply_session = configured
            return configured
        bound = str(await self.get_kv_data(KV_KEY_BILI_REPLY_SESSION, '') or '').strip()
        if bound:
            self._live_bound_reply_session = bound
            return bound
        self._live_bound_reply_session = ''
        fallback = self._default_bili_reply_session_to_self()
        if fallback:
            logger.info('[B站直播] 未显式配置自动回应会话，默认使用 Bot 自己的私聊会话: %s', fallback)
            return fallback
        return ''

    def _default_bili_reply_session_to_self(self) -> str:
        platform_manager = getattr(self.context, 'platform_manager', None)
        try:
            platform_insts = list(platform_manager.get_insts()) if platform_manager else []
        except Exception:
            platform_insts = list(getattr(platform_manager, 'platform_insts', []) or [])
        candidates: list[tuple[int, str, str]] = []
        platform_ids: list[str] = []
        for inst in platform_insts:
            try:
                meta = inst.meta()
                platform_id = str(getattr(meta, 'id', '') or '').strip()
            except Exception:
                platform_id = ''
            if not platform_id:
                platform_id = str(getattr(inst, 'id', '') or getattr(inst, 'name', '') or '').strip()
            if platform_id and platform_id not in platform_ids:
                platform_ids.append(platform_id)
            if not self._is_bili_live_chat_delivery_platform(platform_id):
                continue
            self_id = str(getattr(inst, 'client_self_id', '') or getattr(inst, 'self_id', '') or getattr(inst, 'bot_self_id', '') or '').strip()
            if not platform_id or not self_id or (not self_id.isdigit()):
                continue
            priority = self._bili_live_chat_delivery_platform_priority(platform_id)
            candidates.append((priority, platform_id, self_id))
        if not candidates:
            configured_self_id = self._configured_bot_self_id()
            if not configured_self_id:
                return ''
            platform_id = next((item for item in platform_ids if self._is_bili_live_chat_delivery_platform(item)), '')
            if not platform_id:
                logger.warning('[B站直播] 未找到可作为聊天出口的平台实例，已跳过自动使用 Bot 私聊会话。请在 QQ/聊天目标发送 /bili_live_bind_here。')
                return ''
            return f'{platform_id}:FriendMessage:{configured_self_id}'
        _, platform_id, self_id = sorted(candidates, key=lambda item: item[0])[0]
        return f'{platform_id}:FriendMessage:{self_id}'

    def _bili_auto_reply_priority_types(self) -> set[str]:
        raw = self.config.get('bili_live_auto_reply_rate_limit_exempt_event_types', ['gift', 'super_chat', 'buy_guard'])
        if not isinstance(raw, list):
            raw = ['gift', 'super_chat', 'buy_guard']
        return {str(item).strip() for item in raw if str(item).strip()}

    def _bili_reply_has_named_thanks(self, reply: str, username: str) -> bool:
        if not reply or not username:
            return False
        thanks = '(?:谢谢|感谢|谢啦|多谢)'
        same_clause = '[^，,。.!！?？；;\\n]{0,32}'
        name = re.escape(username)
        return bool(re.search(f'{thanks}{same_clause}{name}', reply) or re.search(f'{name}{same_clause}{thanks}', reply))

    def _build_bili_support_fallback_reply(self, events: list[LiveDanmakuEvent]) -> str:
        reply = self._ensure_bili_support_acknowledgement('', events)
        received: list[str] = []
        for event in events:
            if event.event_type != 'super_chat':
                continue
            username = self._single_line_text(event.username, 30)
            body = self._single_line_text(event.content, 100)
            body = re.sub('^发送醒目留言(?:\\s+[0-9]+(?:\\.[0-9]+)?元)?\\s*[:：]\\s*', '', body).strip()
            if not body:
                continue
            if username and username not in {'系统', '观众'}:
                received.append(f'{username}说的“{body}”我看到啦')
            else:
                received.append(f'你说的“{body}”我看到啦')
        if received:
            reply += ' ' + '；'.join(received) + '。'
        return reply or '谢谢你的醒目留言！'

    def _select_bili_reply_events(self, events: list[LiveDanmakuEvent], max_events: int) -> list[LiveDanmakuEvent]:
        """Keep every guaranteed event, then fill remaining slots with newest normal events."""
        guaranteed = [event for event in events if event.event_type in self._bili_guaranteed_support_types()]
        if not guaranteed:
            return events[-max_events:]
        remaining = max(0, max_events - len(guaranteed))
        normal = [event for event in events if event.event_type not in self._bili_guaranteed_support_types()][-remaining:] if remaining else []
        selected_ids = {id(event) for event in [*guaranteed, *normal]}
        return [event for event in events if id(event) in selected_ids]

    def _is_bili_auto_reply_rate_exempt(self, events: list[LiveDanmakuEvent]) -> bool:
        priority_types = self._bili_auto_reply_priority_types() | self._bili_guaranteed_support_types()
        return any((event.event_type in priority_types for event in events))

    def _bili_auto_reply_rate_limited(self, events: list[LiveDanmakuEvent]) -> bool:
        if self._is_bili_auto_reply_rate_exempt(events):
            return False
        max_per_minute = self._safe_parse_int(self.config.get('bili_live_auto_reply_max_per_minute'), 6)
        if max_per_minute <= 0:
            return False
        now = time.time()
        while self._bili_auto_reply_minute_marks and now - self._bili_auto_reply_minute_marks[0] >= 60:
            self._bili_auto_reply_minute_marks.popleft()
        return len(self._bili_auto_reply_minute_marks) >= max_per_minute

    def _record_bili_auto_reply_rate_mark(self, events: list[LiveDanmakuEvent]) -> None:
        if self._is_bili_auto_reply_rate_exempt(events):
            return
        max_per_minute = self._safe_parse_int(self.config.get('bili_live_auto_reply_max_per_minute'), 6)
        if max_per_minute <= 0:
            return
        now = time.time()
        while self._bili_auto_reply_minute_marks and now - self._bili_auto_reply_minute_marks[0] >= 60:
            self._bili_auto_reply_minute_marks.popleft()
        self._bili_auto_reply_minute_marks.append(now)

    def _record_bili_auto_reply_sent(self, events: list[LiveDanmakuEvent], reply_text: str) -> None:
        self._bili_last_auto_reply_at = time.time()
        self._record_bili_auto_reply_rate_mark(events)
        for event in events:
            if event.event_type in self._bili_guaranteed_support_types() and event.event_id:
                self._bili_acknowledged_support_event_ids.add(event.event_id)
        self._bili_auto_reply_history.append({'ts': self._bili_last_auto_reply_at, 'reply': self._single_line_text(reply_text, 160), 'events': [{'type': event.event_type, 'username': self._single_line_text(event.username, 40), 'content': self._single_line_text(event.content, 120)} for event in events[-5:]]})

    def _bili_live_air_guard_model_enabled(self) -> bool:
        return bool(self.config.get('bili_live_auto_reply_air_guard_model_enabled', True))

    def _bili_live_air_guard_recent_reply_context(self) -> str:
        now = time.time()
        lines: list[str] = []
        for item in list(self._bili_auto_reply_history)[-6:]:
            if not isinstance(item, dict):
                continue
            age = max(0, int(now - self._safe_parse_float(item.get('ts'), 0)))
            reply = self._single_line_text(item.get('reply'), 100)
            if reply:
                lines.append(f'- {age}秒前 Bot：{reply}')
        return '\n'.join(lines)

    async def _bili_live_air_guard_model_decision(self, events: list[LiveDanmakuEvent], session_id: str, local: dict[str, Any]) -> dict[str, Any]:
        return {'reply': bool(local.get('reply')), 'reason': 'local_borderline'}

    async def _should_reply_to_bili_live_events_by_air(self, events: list[LiveDanmakuEvent], session_id: str) -> tuple[bool, str]:
        if not self._bili_live_air_guard_enabled():
            return (True, 'disabled')
        local = self._bili_live_air_guard_local_decision(events)
        if self._is_bili_auto_reply_rate_exempt(events):
            return (True, 'priority_event')
        if not bool(local.get('borderline')):
            return (bool(local.get('reply')), self._single_line_text(local.get('reason'), 80))
        model = await self._bili_live_air_guard_model_decision(events, session_id, local)
        return (bool(model.get('reply')), self._single_line_text(model.get('reason'), 80))

    def _build_bili_live_connectivity_test_reply(self, events: list[LiveDanmakuEvent]) -> str:
        if self._is_bili_auto_reply_rate_exempt(events):
            return ''
        for event in reversed(events[-3:]):
            if event.event_type != 'danmaku':
                continue
            content = re.sub('\\s+', '', str(event.content or '')).strip()
            if not content:
                continue
            username = self._single_line_text(event.username, 16)
            if username in {'系统', '观众'}:
                username = ''
            suffix = f'，{username}' if username else ''
            assistant_mode = self._bili_live_reply_identity_mode() == 'assistant'
            streamer = self._bili_live_streamer_reference()
            if re.search('(听得到|听得见|能听到|能听见|声音.*吗|麦.*吗|麦克风.*吗)', content):
                if assistant_mode:
                    return f'{streamer}这边听得到哦{suffix}～'
                return f'听得到哦{suffix}～'
            if re.search('(看得到|看得见|能看到|能看见|画面.*吗)', content):
                if assistant_mode:
                    return f'{streamer}这边画面看得到哦{suffix}～'
                return f'看得到哦{suffix}～'
            if re.fullmatch('(在吗|还在吗|在不在|星缘在吗|主播在吗)', content):
                if assistant_mode:
                    return f'在的，我帮{streamer}看着弹幕呢{suffix}～'
                return f'在的哦{suffix}～'
            if re.search('(测试|test|TEST|试播)', content):
                if assistant_mode:
                    return f'测试收到，{streamer}这边正常{suffix}～'
                return f'测试收到{suffix}～'
        return ''

    async def _send_bili_live_prebuilt_reply(self, session_id: str, selected: list[LiveDanmakuEvent], reply_text: str, *, log_reason: str='') -> None:
        reply_text = self._ensure_bili_support_acknowledgement(reply_text, selected)
        force_voice = bool(self.config.get('bili_live_auto_reply_force_full_tts', True))
        chain = await self._decorate_bili_live_reply_chain(session_id, [Plain(reply_text)], force_voice=False, skip_subtitle=force_voice)
        chain = self._strip_tts_blocks_from_plain_chain(chain)
        chain = self._ensure_visible_text_after_voice(chain, reply_text)
        synced_tts = force_voice and self._bili_live_auto_reply_sync_tts_subtitle()
        if synced_tts:
            sent_synced = await self._send_bili_live_synced_tts_reply(session_id, reply_text)
            if not sent_synced:
                await self.context.send_message(session_id, MessageChain(chain))
                await self._push_subtitle(reply_text, source='bili_live')
        else:
            await self.context.send_message(session_id, MessageChain(chain))
            await self._push_subtitle(reply_text, source='bili_live')
        self._record_bili_auto_reply_sent(selected, reply_text)
        if force_voice and (not synced_tts):
            asyncio.create_task(self._send_bili_live_tts_followup(session_id, reply_text, push_subtitle=False))
        logger.info('[B站直播] 已发送预置自动回应%s -> %s: %s', f'({log_reason})' if log_reason else '', session_id, reply_text)

    async def _reply_to_bili_live_events(self, events: list[LiveDanmakuEvent]) -> None:
        session_id = await self._get_bili_reply_session()
        if not session_id:
            logger.warning('[B站直播] 已收到弹幕，但未绑定自动回应会话，也未能自动获取 Bot 自己的 QQ。请在目标聊天发送 /bili_live_bind_here。')
            return
        if self._bili_auto_reply_rate_limited(events):
            logger.info('[B站直播] 普通弹幕自动回应已达到每分钟上限，跳过本批 %s 条事件。', len(events))
            return
        should_reply, air_reason = await self._should_reply_to_bili_live_events_by_air(events, session_id)
        if not should_reply:
            logger.info('[B站直播] 读空气判定本批弹幕无需自动回应: reason=%s events=%s', air_reason, len(events))
            return
        max_events = max(1, self._safe_parse_int(self.config.get('bili_live_auto_reply_max_events'), 5))
        selected_for_prebuilt = self._select_bili_reply_events(events, max_events)
        prebuilt_reply = self._build_bili_live_connectivity_test_reply(selected_for_prebuilt)
        if prebuilt_reply:
            await self._send_bili_live_prebuilt_reply(session_id, selected_for_prebuilt, prebuilt_reply, log_reason='connectivity_test')
            return
        reply_mode = str(self.config.get('bili_live_auto_reply_mode') or 'native').strip()
        if reply_mode == 'native':
            if await self._reply_to_bili_live_events_via_framework(events, session_id):
                return
            logger.warning('[B站直播] 框架式原生自动回应失败，回退到直接 LLM 自动回应。')
        provider = None
        try:
            provider = self.context.get_using_provider(session_id)
        except Exception:
            try:
                provider = self.context.get_using_provider()
            except Exception:
                provider = None
        if not provider:
            logger.warning('[B站直播] 自动回应弹幕失败：未找到可用 LLM Provider')
            return
        max_events = max(1, self._safe_parse_int(self.config.get('bili_live_auto_reply_max_events'), 5))
        selected = self._select_bili_reply_events(events, max_events)
        formatted = self._format_bili_events(selected)
        if not formatted:
            return
        system_prompt = str(self.config.get('bili_live_auto_reply_system_prompt') or '延续当前选定的人格和直播身份，结合最近的弹幕自然接话。像实时聊天一样简洁，优先回应具体问题或有意思的反馈；不逐条复读，也不必每条都回应。')
        persona_prompt = await live_private_persona_prompt(self, session_id)
        if persona_prompt:
            system_prompt += '\n\n' + persona_prompt
        prompt = f'参考这批 B 站直播间的最新互动，挑一处最值得回应的内容。\n{self._bili_live_reply_identity_prompt_line()}\n用中文按当前直播身份自然接话，优先回应具体问题或反馈；不必逐条回复，也不要每次都用欢迎语开场。回复通常保持简短（约 15 至 60 字），复杂问题可适当展开。只输出给直播间观众的话，不描述处理过程或发送状态。\n\n{formatted}'
        prompt += self._build_bili_support_reply_hint(selected)
        auxiliary_context = await self._build_bili_live_auxiliary_context(selected)
        if auxiliary_context:
            prompt += '\n\n' + auxiliary_context
        response = await tracked_text_chat(self, provider, task='live_bili_reply', prompt=prompt,
                                          system_prompt=system_prompt, session_id=f'{session_id}:bili_live_auto_reply', persist=False)
        reply_text = self._extract_provider_text(response)
        reply_text = self._clean_auto_reply_text(reply_text)
        if not reply_text:
            return
        reply_text = self._ensure_bili_support_acknowledgement(reply_text, selected)
        force_voice = bool(self.config.get('bili_live_auto_reply_force_full_tts', True))
        chain = await self._decorate_bili_live_reply_chain(session_id, [Plain(reply_text)], force_voice=False, skip_subtitle=force_voice)
        chain = self._strip_tts_blocks_from_plain_chain(chain)
        chain = self._ensure_visible_text_after_voice(chain, reply_text)
        synced_tts = force_voice and self._bili_live_auto_reply_sync_tts_subtitle()
        if synced_tts:
            sent_synced = await self._send_bili_live_synced_tts_reply(session_id, reply_text)
            if not sent_synced:
                await self.context.send_message(session_id, MessageChain(chain))
                await self._push_subtitle(reply_text, source='bili_live')
        else:
            await self.context.send_message(session_id, MessageChain(chain))
            await self._push_subtitle(reply_text, source='bili_live')
        self._record_bili_auto_reply_sent(selected, reply_text)
        if force_voice and (not synced_tts):
            asyncio.create_task(self._send_bili_live_tts_followup(session_id, reply_text, push_subtitle=False))
        logger.info(f'[B站直播] 已自动回应弹幕 -> {session_id}: {reply_text}')

    async def _reply_to_bili_live_events_via_framework(self, events: list[LiveDanmakuEvent], session_id: str) -> bool:
        started_at = time.perf_counter()
        max_events = max(1, self._safe_parse_int(self.config.get('bili_live_auto_reply_max_events'), 5))
        selected = self._select_bili_reply_events(events, max_events)
        formatted = self._format_bili_events(selected)
        if not formatted:
            return False
        try:
            session = MessageSession.from_str(session_id)
        except Exception as e:
            logger.warning(f'[B站直播] 无法解析自动回应会话: {session_id} err={e}')
            return False
        try:
            t_conv = time.perf_counter()
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(session_id)
            if not curr_cid:
                curr_cid = await self.context.conversation_manager.new_conversation(session_id, title='B站直播自动回应')
                logger.info(f'[B站直播] 已为自动回应会话创建对话: {session_id}')
            conv = await self.context.conversation_manager.get_conversation(session_id, curr_cid)
            if not conv:
                logger.warning(f'[B站直播] 自动回应会话无法读取对话: {session_id}')
                return False
            conv_elapsed = time.perf_counter() - t_conv
        except Exception as e:
            logger.warning(f'[B站直播] 读取自动回应会话对话失败: {e}')
            return False
        prompt = f'【B站直播互动】\n{self._bili_live_reply_identity_prompt_line()}\n下方昵称属于直播间观众，不是当前私聊对象；只把与本轮互动有关的内容带入回复。挑一处最值得回应的弹幕，用自然中文接话；欢迎、问候和“摸摸/贴贴/抱抱”等互动按语境回应，不要逐条复读或把互动误说成第三者的行为。只输出给直播间观众的话，不描述处理过程或发送状态。\n\n{formatted}'
        prompt += self._build_bili_support_reply_hint(selected)
        auxiliary_context = await self._build_bili_live_auxiliary_context(selected)
        if auxiliary_context:
            prompt += '\n\n' + auxiliary_context
        persona_prompt = await live_private_persona_prompt(self, session_id)
        if persona_prompt:
            prompt += '\n\n' + persona_prompt
        if self.config.get('bili_live_auto_reply_force_full_tts', True):
            prompt += '\n\n请只输出普通文本回复，不要调用工具，不要写 <record>、<voice>、<语音>、<send_message_to_user> 等标签；如果需要语音，系统 TTS 插件会自动处理。'
        try:
            synthetic_event = SyntheticBiliLiveWakeEvent(template_event=self._bili_reply_event_template, context=self.context, session=session, message=self._build_bili_live_memory_recall_query(selected) or 'bili_live_auto_reply_wakeup')
            synthetic_event.set_extra('bili_live_auto_reply', True)
            synthetic_event.set_extra('bili_live_events', [event.raw for event in selected])
            cfg = self.context.get_config(umo=session_id)
            provider_settings = cfg.get('provider_settings', {}) if isinstance(cfg, dict) else {}
            if not callable(build_main_agent) or MainAgentBuildConfig is None:
                logger.warning('[B站直播] 当前 AstrBot 没有兼容的主 Agent 构建入口，跳过框架式自动回应。')
                return False
            build_cfg = MainAgentBuildConfig(tool_call_timeout=int(provider_settings.get('tool_call_timeout', 120) or 120), llm_safety_mode=False, streaming_response=False)
            req = ProviderRequest(prompt=prompt, conversation=conv, session_id=session_id)
            t_build = time.perf_counter()
            result = await build_main_agent(event=synthetic_event, plugin_context=self.context, config=build_cfg, req=req)
            if not result:
                return False
            build_elapsed = time.perf_counter() - t_build
            runner = result.agent_runner
            t_llm = time.perf_counter()
            async for _ in runner.step_until_done(8):
                pass
            llm_elapsed = time.perf_counter() - t_llm
            llm_resp = runner.get_final_llm_resp()
            if not llm_resp or llm_resp.role != 'assistant':
                return False
            reply_text = self._clean_auto_reply_text(llm_resp.completion_text or '')
            if not reply_text:
                return False
            if 'BiliBot 活动（已直接读取）' in auxiliary_context and re.search('(让我|等我).{0,6}(查|找|想|看)|我来查一下|我看看记录', reply_text):
                logger.warning('[B站直播] native 回复仍停留在查询动作，回退直接生成最终回答。')
                return False
            reply_text = self._ensure_bili_support_acknowledgement(reply_text, selected)
            t_decorate = time.perf_counter()
            force_voice = bool(self.config.get('bili_live_auto_reply_force_full_tts', True))
            chain = await self._decorate_bili_live_reply_chain(session_id, [Plain(reply_text)], force_voice=False, skip_subtitle=force_voice)
            decorate_elapsed = time.perf_counter() - t_decorate
            chain = self._strip_tts_blocks_from_plain_chain(chain)
            chain = self._ensure_visible_text_after_voice(chain, reply_text)
            t_send = time.perf_counter()
            synced_tts = force_voice and self._bili_live_auto_reply_sync_tts_subtitle()
            if synced_tts:
                sent_synced = await self._send_bili_live_synced_tts_reply(session_id, reply_text)
                if not sent_synced:
                    await self.context.send_message(session_id, MessageChain(chain))
                    await self._push_subtitle(reply_text, source='bili_live')
            else:
                await self.context.send_message(session_id, MessageChain(chain))
                await self._push_subtitle(reply_text, source='bili_live')
            send_elapsed = time.perf_counter() - t_send
            self._record_bili_auto_reply_sent(selected, reply_text)
            if force_voice and (not synced_tts):
                asyncio.create_task(self._send_bili_live_tts_followup(session_id, reply_text, push_subtitle=False))
            total_elapsed = time.perf_counter() - started_at
            logger.info('[B站直播] 自动回应耗时: total=%.2fs conv=%.2fs build=%.2fs llm=%.2fs decorate_tts=%.2fs send=%.2fs session=%s', total_elapsed, conv_elapsed, build_elapsed, llm_elapsed, decorate_elapsed, send_elapsed, session_id)
            logger.info(f'[B站直播] 已通过完整框架链路自动回应弹幕 -> {session_id}: {reply_text}')
            return True
        except Exception as e:
            logger.warning(f'[B站直播] 框架式原生自动回应失败: {e}')
            return False

    async def _decorate_bili_live_reply_chain(self, session_id: str, chain: list[Any], force_voice: bool=False, skip_subtitle: bool=False) -> list[Any]:
        if not chain:
            return chain
        if force_voice:
            chain = self._wrap_plain_chain_as_tts(chain)
        try:
            session = MessageSession.from_str(session_id)
            message_obj = AstrBotMessage()
            message_obj.type = session.message_type
            message_obj.self_id = session.session_id
            message_obj.session_id = session.session_id
            message_obj.message_id = f'bili_live_reply_{uuid.uuid4().hex}'
            message_obj.sender = MessageMember(user_id=session.session_id)
            message_obj.message = chain
            message_obj.message_str = ''
            message_obj.raw_message = None
            message_obj.timestamp = int(time.time())
            platform_meta = None
            if self._bili_reply_event_template:
                try:
                    platform_meta = self._bili_reply_event_template.get_platform_metadata()
                except Exception:
                    platform_meta = None
            if platform_meta is None:
                platform_meta = PlatformMetadata(name=session.platform_id, description='SyntheticBiliLiveReply', id=session.platform_id)
            event = AstrMessageEvent('', message_obj, platform_meta, message_obj.session_id)
            if skip_subtitle:
                event.set_extra('bili_live_skip_subtitle', True)
            event.set_result(self._build_message_result_from_chain(chain))
        except Exception as e:
            logger.debug(f'[B站直播] 构造自动回应装饰事件失败，跳过 hooks: {e}')
            return chain
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(EventType.OnDecoratingResultEvent)
        except Exception as e:
            logger.debug(f'[B站直播] 获取装饰 hooks 失败: {e}')
            return chain
        if force_voice:
            self._mark_tts_modify_forced_voice(event, handlers)
        for handler in handlers:
            try:
                await handler.handler(event)
            except Exception as e:
                logger.warning('[B站直播] 自动回应装饰 hook 失败: %s: %s', getattr(handler, 'handler_full_name', 'unknown'), e)
        result = event.get_result()
        processed = getattr(result, 'chain', None) if result is not None else None
        processed_chain = list(processed or chain)
        if force_voice and (not any((isinstance(component, Record) for component in processed_chain))):
            spoken = self._plain_chain_text(processed_chain)
            record_chain = await self._build_bili_live_tts_chain(session_id, spoken)
            if record_chain:
                return record_chain
        return processed_chain

    def _plain_chain_text(self, chain: list[Any]) -> str:
        parts: list[str] = []
        for component in chain:
            if isinstance(component, Plain):
                text = str(getattr(component, 'text', '') or '').strip()
                text = self._strip_tts_blocks_from_text(text)
                if text:
                    parts.append(text)
        return '\n'.join(parts).strip()

    def _extract_provider_text(self, response) -> str:
        if response is None:
            return ''
        if isinstance(response, str):
            return response.strip()
        for attr in ('completion_text', 'content', 'text', 'message'):
            value = getattr(response, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(response).strip()

    def _clean_auto_reply_text(self, text: str) -> str:
        cleaned = (text or '').strip()
        cleaned = re.sub('^```[A-Za-z0-9_-]*\\s*', '', cleaned)
        cleaned = re.sub('\\s*```$', '', cleaned)
        cleaned = cleaned.strip().strip('"“”')
        cleaned = self._strip_bili_reply_control_markup(cleaned)
        cleaned = self._strip_bili_meta_reply_lines(cleaned)
        max_length = self._safe_parse_int(self.config.get('bili_live_auto_reply_max_length'), 80)
        if max_length > 0 and len(cleaned) > max_length:
            cleaned = cleaned[:max_length].rstrip() + '...'
        return cleaned

    def _strip_bili_meta_reply_lines(self, text: str) -> str:
        lines = [line.strip() for line in str(text or '').splitlines()]
        kept: list[str] = []
        for line in lines:
            if not line:
                continue
            if self._is_bili_meta_reply_line(line):
                continue
            kept.append(line)
        return '\n'.join(kept).strip()

    def _strip_bili_reply_control_markup(self, text: str) -> str:
        cleaned = str(text or '')
        if not cleaned:
            return ''
        cleaned = re.sub('(?is)<\\s*(send_message_to_user|astrbot_execute_shell|astrbot_execute_python)\\b.*$', '', cleaned)
        cleaned = re.sub('(?is)<\\s*message\\s*>(.*?)<\\s*/\\s*message\\s*>', '\\1', cleaned)
        cleaned = re.sub('(?is)<\\s*(record|voice|tts|\\u8bed\\u97f3)\\b[^>]*>(.*?)<\\s*/\\s*\\1\\s*>', '\\2', cleaned)
        cleaned = re.sub('(?is)<\\s*/?\\s*(record|voice|tts|\\u8bed\\u97f3|message)\\b[^>]*>', '', cleaned)
        cleaned = re.sub('(?is)<\\s*/?\\s*parameter\\b[^>]*>', '', cleaned)
        cleaned = re.sub('(?is)<[^>\\n]{1,120}>', '', cleaned)
        cleaned = re.sub('\\[语音\\]|\\[voice\\]|\\[record\\]', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub('\\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def _is_bili_meta_reply_line(self, line: str) -> bool:
        compact = re.sub('\\s+', '', str(line or ''))
        if not compact:
            return True
        meta_patterns = ('消息已经发出', '消息已发出', '已经发出去了', '已经发送', '已发送', '我已经回应', '我刚刚回应', '温柔地回应', '希望没有冷落', '不要冷落', '处理了这条弹幕', '这条弹幕我没太看懂', '这条弹幕我没有太看懂', '弹幕我没太看懂', '弹幕我没有太看懂')
        return any((pattern in compact for pattern in meta_patterns))

    def _dedupe_repeated_plain_text(self, text: str) -> str:
        cleaned = str(text or '').strip()
        if not cleaned:
            return ''
        compact = re.sub('\\s+', '', cleaned)
        if len(compact) % 2 == 0:
            half = len(compact) // 2
            if compact[:half] == compact[half:]:
                return cleaned[:max(1, len(cleaned) // 2)].strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(lines) == 2 and lines[0] == lines[1]:
            return lines[0]
        return cleaned

    @staticmethod
    def _single_line_text(value: Any, limit: int=120) -> str:
        text = re.sub('\\s+', ' ', str(value or '')).strip()
        if limit > 0 and len(text) > limit:
            text = text[:limit].rstrip() + '...'
        return text

    def _build_native_live_relationship_context(self, events: list[LiveDanmakuEvent]) -> str:
        if not self._live_relationship_context_enabled():
            return ''
        plugin = self._get_v2_live_state_owner()
        if plugin is None:
            return ''
        lines: list[str] = []
        seen_users: set[str] = set()
        max_users = max(1, self._safe_parse_int(self._live_setting('live_relationship_context_max_users', 'private_companion_live_context_max_users', 3), 3))
        for live_event in reversed(events):
            if live_event.event_type not in {'danmaku', 'gift', 'super_chat', 'buy_guard', 'enter_room', 'follow', 'like'}:
                continue
            match = self._match_private_companion_member(plugin, live_event.username, live_event)
            if not match or not match.get('identity_verified'):
                continue
            viewer_key = self._live_viewer_key(live_event)
            if viewer_key in seen_users:
                continue
            seen_users.add(viewer_key)
            display_name = self._single_line_text(match.get('name'), 40) or live_event.username
            alias_text = '、'.join((item for item in [*list(match.get('aliases') or []), *list(match.get('observed_names') or [])][:5] if item))
            activity = self._private_companion_viewer_activity_for_context(plugin, live_event.username, event=live_event)
            detail = f'- 观众 `{live_event.username}` 的本地直播称呼：{display_name}'
            if alias_text:
                detail += f'；可识别名称/别名：{alias_text}'
            style_hint = self._private_companion_relationship_style_hint(match)
            if style_hint:
                detail += f'；称呼/互动风格：{style_hint}'
            if self._live_setting('live_relationship_context_include_identity_note', 'private_companion_live_context_include_identity_note', False) and match.get('identity_note'):
                detail += f"；身份备注：{self._single_line_text(match.get('identity_note'), 80)}"
            lines.append(detail)
            if activity:
                lines.append(f'  - 直播活跃画像：{activity}')
            if len(seen_users) >= max_users:
                break
        if not lines:
            return ''
        return '## 直播观众关系资料\n以下来自 V2 本地直播资料，按当前平台账号关联。它只用于自然称呼、延续公开直播互动；不要说出账号、内部字段、匹配过程或私密资料。直播昵称不证明私聊身份，没有关联时按普通观众回应。\n' + '\n'.join(lines)

    # Compatibility for older V2 development callers; always uses local Live data.
    _build_private_companion_live_context = _build_native_live_relationship_context

    def _private_companion_viewer_activity_for_context(self, plugin: Any, live_username: str, *, user_id: str='', event: LiveDanmakuEvent | None=None) -> str:
        if not self._live_setting('live_viewer_activity_context_enabled', 'private_companion_viewer_activity_context_enabled', True):
            return ''
        platform = self._live_event_platform(event)
        store = self._v2_live_state_store(plugin, platform=platform)
        activity_map = store.get('viewer_activity')
        if not isinstance(activity_map, dict):
            return ''
        keys = [self._live_viewer_key(event, live_username, platform=platform)]
        # Old V2 snapshots without platform account IDs kept public viewer
        # activity under a display name. Never use that fallback for known IDs.
        if platform == 'bili' and not self._private_companion_live_external_ids(live_username, event):
            keys.append(f'live:{live_username}')
        item = None
        for key in keys:
            candidate = activity_map.get(key)
            if isinstance(candidate, dict):
                item = candidate
                break
        if not isinstance(item, dict):
            return ''
        total = self._safe_parse_int(item.get('total_events'), 0)
        if total <= 0:
            return ''
        event_counts = item.get('event_counts') if isinstance(item.get('event_counts'), dict) else {}
        highlights: list[str] = [f'累计互动 {total} 次']
        danmaku_count = self._safe_parse_int(event_counts.get('danmaku'), 0)
        if danmaku_count:
            highlights.append(f'弹幕 {danmaku_count} 条')
        gift_count = sum((self._safe_parse_int(event_counts.get(kind), 0) for kind in ('gift', 'super_chat', 'buy_guard')))
        if gift_count:
            highlights.append(f'重要互动 {gift_count} 次')
        first_seen = self._safe_parse_float(item.get('first_seen'), 0)
        if first_seen:
            days = max(0, int((time.time() - first_seen) / 86400))
            if days >= 1:
                highlights.append(f'已出现约 {days} 天')
        recent_danmaku = item.get('recent_danmaku') if isinstance(item.get('recent_danmaku'), list) else []
        if recent_danmaku:
            max_age = max(30.0, self._safe_parse_float(self._live_setting('live_relationship_context_max_age_seconds', 'private_companion_live_context_max_age_seconds', 900.0), 900.0))
            limit = max(1, self._safe_parse_int(self._live_setting('live_relationship_context_recent_limit', 'private_companion_live_context_recent_limit', 3), 3))
            recent = [row for row in recent_danmaku if isinstance(row, dict)
                      and time.time() - self._safe_parse_float(row.get('ts'), 0) <= max_age]
            samples = [self._single_line_text(row.get('content'), 36) for row in recent[:limit]]
            samples = [item for item in samples if item]
            if samples:
                highlights.append('最近常聊：' + ' / '.join(samples))
        return '；'.join(highlights)

    def _live_memory_context_enabled(self) -> bool:
        return bool(self.config.get('live_memory_context_enabled', True))

    async def _build_bili_live_auxiliary_context(self, events: list[LiveDanmakuEvent]) -> str:
        parts = [self._build_bili_live_identity_instruction(), self._build_live_reply_experience_guard(events),
                 self._build_bili_live_continuity_context(events),
                 self._build_live_stream_memory_context(events),
                 self._build_native_live_relationship_context(events)]
        return '\n\n'.join((part for part in parts if part))

    def _build_live_reply_experience_guard(self, events: list[LiveDanmakuEvent]) -> str:
        if not events:
            return ''
        usernames = []
        for event in events[-3:]:
            name = self._single_line_text(event.username, 30)
            if name and name not in {'系统', '观众'} and (name not in usernames):
                usernames.append(name)
        name_hint = '、'.join(usernames)
        support_hint = ('本批有礼物/SC/上舰时，以主播助理身份感谢具体观众支持主播，再接一句现场协助回应。' if self._bili_live_reply_identity_mode() == 'assistant' else '本批有礼物/SC/上舰时，先自然感谢具体观众，再接一句现场回应。') if any((event.event_type in {'gift', 'super_chat', 'buy_guard'} for event in events)) else '优先回应最新弹幕里最具体的问题、情绪或梗。'
        return f'## 直播回应体验原则\n{support_hint}记忆、关系网和观众画像只作为背景，不要盖过当前弹幕；除非当前内容确实在延续旧话题，否则不要每次都像老熟人重逢，也不要反复说“好久不见”。如果有候选匹配但不确定，就按普通观众口吻回应；回复要像主播现场顺嘴接话，短、暖、具体，不解释自己用了哪些插件或记忆。' + (f' 当前可点名观众：{name_hint}。' if name_hint else '')

    async def _build_living_memory_live_context(self, session_id: str, events: list[LiveDanmakuEvent]) -> str:
        # The native live state store is the sole source for this context.  The
        # method name remains for compatibility with older callers, but there
        # is no LivingMemory plugin lookup or cross-plugin search here.
        context = self._build_live_stream_memory_context(events)
        if not context:
            return ''
        return context.replace('## 直播专用记忆', '## 直播连续记忆', 1)

    def _build_bili_live_continuity_context(self, events: list[LiveDanmakuEvent]) -> str:
        if not events:
            return ''
        session_events = [item for item in self._bili_session_events if item.event_type in {'danmaku', 'gift', 'super_chat', 'buy_guard', 'follow', 'enter_room'}]
        if len(session_events) <= len(events):
            return ''
        current_keys = {(item.event_type, item.username, item.content, int(item.ts * 1000)) for item in events}
        current_names: list[str] = []
        for item in events:
            name = self._single_line_text(item.username, 40)
            if name and name != '系统' and (name not in current_names):
                current_names.append(name)
        lines: list[str] = []
        max_viewers = max(1, self._safe_parse_int(self.config.get('bili_live_continuity_context_max_viewers'), 3))
        max_per_viewer = max(1, self._safe_parse_int(self.config.get('bili_live_continuity_context_per_viewer'), 4))
        now = time.time()
        for name in current_names[:max_viewers]:
            prior: list[LiveDanmakuEvent] = []
            for item in reversed(session_events):
                key = (item.event_type, item.username, item.content, int(item.ts * 1000))
                if key in current_keys:
                    continue
                if item.username != name:
                    continue
                if item.event_type not in {'danmaku', 'gift', 'super_chat', 'buy_guard'}:
                    continue
                prior.append(item)
                if len(prior) >= max_per_viewer:
                    break
            if not prior:
                continue
            snippets = []
            for item in reversed(prior):
                age = max(0, int(now - item.ts))
                snippets.append(f'{age}秒前{item.event_type}: {self._single_line_text(item.content, 48)}')
            lines.append(f'- {name} 本场前文：' + '；'.join(snippets))
        recent_room = []
        for item in reversed(session_events):
            key = (item.event_type, item.username, item.content, int(item.ts * 1000))
            if key in current_keys:
                continue
            if item.event_type != 'danmaku':
                continue
            text = self._single_line_text(item.display_text(), 64)
            if text:
                recent_room.append(text)
            if len(recent_room) >= 5:
                break
        if recent_room:
            lines.append('- 直播间刚聊过：' + '；'.join(reversed(recent_room)))
        if not lines:
            return ''
        return '## 本场连续对话上下文\n下面是直播间本场已经发生过的近距离互动，用于承接同一观众的前文。如果当前弹幕像是在接着聊，请直接顺着前文回应；不要把连续发言当作观众刚来，也不要机械复述这些上下文。\n' + '\n'.join(lines)

    def _build_live_stream_memory_context(self, events: list[LiveDanmakuEvent]) -> str:
        if not self._live_memory_enabled() or not self._live_memory_context_enabled():
            return ''
        plugin = self._get_v2_live_state_owner()
        if plugin is None:
            return ''
        platform = self._live_event_platform(events[-1] if events else None)
        store = self._v2_live_state_store(plugin, platform=platform)
        if not store:
            return ''
        max_lines = max(4, self._safe_parse_int(self.config.get('live_memory_context_max_lines'), 12))
        lines: list[str] = []
        session_line = self._live_memory_session_line(platform=platform)
        if session_line:
            lines.append(f'- 本场状态：{session_line}')
        for item in self._live_memory_recent_items(store, limit=3):
            lines.append(f'- 可承接记忆：{item}')
            if len(lines) >= max_lines:
                break
        if len(lines) < max_lines:
            for item in self._live_memory_recent_highlights(store, limit=3):
                lines.append(f'- 最近高光：{item}')
                if len(lines) >= max_lines:
                    break
        if len(lines) < max_lines:
            topics = self._live_memory_topic_lines(store, limit=4)
            if topics:
                lines.append('- 常见话题：' + '；'.join(topics))
        if len(lines) < max_lines:
            threads = self._live_memory_open_thread_lines(store, limit=3)
            if threads:
                lines.append('- 未完话题：' + '；'.join(threads))
        if len(lines) < max_lines:
            for item in self._live_memory_viewer_lines(plugin, events, limit=3):
                lines.append(f'- 观众记忆：{item}')
                if len(lines) >= max_lines:
                    break
        if not lines:
            return ''
        return '## 直播专用记忆上下文\n以下是专门为直播场景整理的记忆，只用于让回复更像连续直播互动。可以自然承接常聊话题、高光和未完梗；不要说出内部字段、存储位置或分析过程；没有把握时只当作轻量背景，不要强行认亲或编造事实。\n' + '\n'.join(lines[:max_lines])

    def _build_bili_support_reply_hint(self, events: list[LiveDanmakuEvent]) -> str:
        if not any((event.event_type in {'gift', 'super_chat'} for event in events)):
            return ''
        super_chats = [event for event in events if event.event_type == 'super_chat']
        sc_requirement = ''
        if super_chats:
            rows = '；'.join((f'{self._single_line_text(event.username, 30)}：{self._single_line_text(event.content, 100)}' for event in super_chats))
            sc_requirement = f'本批 SC 必须逐一说出发送者昵称并致谢；致谢后还要针对 SC 正文里的问题、观点或情绪做具体回应，不能只说谢谢。SC：{rows}。'
        return '\n\n本批直播事件包含礼物或醒目留言。请优先感谢送礼物/SC 的观众，自然提到观众名和礼物或 SC 内容；不要机械复读数量，不要像播报清单。' + sc_requirement

    async def _inject_bili_live_context(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        from ..request_context import set_turn_context

        set_turn_context(req, 'bili_live')
        if not self.config.get('bili_live_inject_enabled', True):
            return
        if not self._is_bili_live_enabled():
            return
        if not self._is_bili_live_running():
            return
        include_events = self.config.get('bili_live_inject_event_types', ['danmaku'])
        if not isinstance(include_events, list):
            include_events = ['danmaku']
        events = self._recent_bili_events(include_events=include_events)
        formatted = self._format_bili_events(events)
        if not formatted:
            return
        prompt = f'## B站直播间实时信息\n以下是你当前可以读取到的最近 B站直播间事件。它们是实时上下文，不一定需要逐条回应；当用户要求你看弹幕、回应直播间观众，或当前对话和直播互动相关时，可以自然引用。\n不要伪造未列出的弹幕、礼物或观众行为。\n{formatted}'
        auxiliary_context = await self._build_bili_live_auxiliary_context(events)
        if auxiliary_context:
            prompt += '\n\n' + auxiliary_context
        set_turn_context(req, 'bili_live', prompt)

    async def _format_bili_history_fallback(self, limit: int=10) -> str:
        client = self._bili_live_client
        fetcher = getattr(client, 'fetch_recent_history_events', None)
        if not fetcher:
            return ''
        try:
            events = await fetcher(limit)
        except Exception as e:
            logger.debug(f'[B站直播] 读取历史弹幕兜底失败: {e}')
            return ''
        return self._format_bili_events(events)
