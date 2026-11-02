"""V2 reality model services."""
from __future__ import annotations
from .common import *

class RealityModelMethods:

    def _provider_supports_image(self, provider: Any) -> bool:
        if provider is None or not callable(getattr(provider, 'text_chat', None)):
            return False
        config = getattr(provider, 'provider_config', None) or getattr(provider, 'config', None) or {}
        modalities = config.get('modalities') if isinstance(config, dict) else getattr(config, 'modalities', None)
        if isinstance(modalities, (list, tuple, set)):
            return 'image' in modalities
        return True

    @staticmethod
    def _provider_runtime_id(provider: Any) -> str:
        config = getattr(provider, 'provider_config', None) or getattr(provider, 'config', None) or {}
        for value in (
            config.get('id') if isinstance(config, dict) else getattr(config, 'id', ''),
            config.get('provider_id') if isinstance(config, dict) else getattr(config, 'provider_id', ''),
            getattr(provider, 'provider_id', ''),
            getattr(provider, 'id', ''),
        ):
            cleaned = _single_line(value, 160)
            if cleaned:
                return cleaned
        return ''

    @staticmethod
    def _provider_label(provider: Any) -> str:
        try:
            meta = provider.meta() if callable(getattr(provider, 'meta', None)) else None
        except Exception:
            meta = None
        for source in (meta, getattr(provider, 'provider_config', None), getattr(provider, 'config', None)):
            if isinstance(source, dict):
                label = _single_line(source.get('model') or source.get('name') or source.get('id') or source.get('type'), 100)
            else:
                label = _single_line(getattr(source, 'model', '') or getattr(source, 'name', '') or
                                     getattr(source, 'id', '') or getattr(source, 'type', ''), 100)
            if label:
                return label
        return _single_line(provider.__class__.__name__, 100)

    @staticmethod
    def _provider_declared_image_capability(provider: Any) -> bool | None:
        config = getattr(provider, 'provider_config', None) or getattr(provider, 'config', None) or {}
        sources = (provider, config)
        for source in sources:
            modalities = source.get('modalities') if isinstance(source, dict) else getattr(source, 'modalities', None)
            if isinstance(modalities, (list, tuple, set)):
                normalized = {str(item or '').strip().lower() for item in modalities}
                if not normalized:
                    return True
                return 'image' in normalized or 'vision' in normalized
            for key in ('supports_image', 'support_image', 'is_multimodal', 'multimodal'):
                value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
                if isinstance(value, bool):
                    return value
        try:
            meta = provider.meta() if callable(getattr(provider, 'meta', None)) else None
        except Exception:
            meta = None
        modalities = meta.get('modalities') if isinstance(meta, dict) else getattr(meta, 'modalities', None)
        if isinstance(modalities, (list, tuple, set)):
            normalized = {str(item or '').strip().lower() for item in modalities}
            return 'image' in normalized or 'vision' in normalized
        return None

    def _vision_provider_options(self) -> list[dict[str, str]]:
        providers: list[Any] = []
        seen_objects: set[int] = set()
        for getter_name in ('get_all_providers',):
            getter = getattr(self.context, getter_name, None)
            if not callable(getter):
                continue
            try:
                result = getter()
                if not inspect.isawaitable(result):
                    providers.extend(list(result or []))
            except Exception as exc:
                logger.debug('[RealityCompanion] 读取视觉模型列表失败: getter=%s error_type=%s',
                             getter_name, type(exc).__name__)
        manager = getattr(self.context, 'provider_manager', None)
        inst_map = getattr(manager, 'inst_map', None)
        if isinstance(inst_map, dict):
            providers.extend(inst_map.values())

        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for provider in providers:
            if id(provider) in seen_objects:
                continue
            seen_objects.add(id(provider))
            if not self._provider_supports_image(provider):
                continue
            if self._provider_declared_image_capability(provider) is False:
                continue
            provider_id = self._provider_runtime_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            label = self._provider_label(provider)
            options.append({'id': provider_id, 'label': f'{label} · {provider_id}' if label != provider_id else provider_id})
        return sorted(options, key=lambda item: (item['label'].casefold(), item['id'].casefold()))

    def _record_llm_usage(self, *, provider_id='', task='', prompt='', completion='', elapsed_ms=0,
                          success=False, error='', resp=None, response=None) -> None:
        tracker = getattr(self, 'token_usage', None)
        if tracker is None:
            return
        try:
            tracker.record(provider_id=provider_id, task=task, prompt=prompt, completion=completion,
                elapsed_ms=elapsed_ms, success=success, error=error,
                response=resp if resp is not None else response, persist=False)
            pending = getattr(self, '_token_usage_save_task', None)
            if pending is None or pending.done():
                self._token_usage_save_task = self._create_lifecycle_background_task(
                    asyncio.to_thread(tracker.save, force=True), label='token_usage')
        except Exception as exc:
            logger.warning('[RealityCompanion] 模型用量统计暂不可用 error_type=%s', type(exc).__name__)

    async def _llm_call(self, prompt: str, **kwargs: Any) -> str:
        api = self.v2_host_api()
        caller = getattr(api, 'generate_reality_touch_text', None) if api is not None else None
        if not callable(caller):
            return ''
        return str(await caller(prompt, **kwargs) or '')

    async def _resolve_proactive_persona_prompt(self, user: dict[str, Any], **kwargs: Any) -> str:
        api = self.v2_host_api()
        getter = getattr(api, 'get_realtime_context', None) if api is not None else None
        if not callable(getter):
            return ''
        result = await getter(_single_line(user.get('user_id'), 120), purpose='reality_touch',
                              umo=_single_line(kwargs.get('umo'), 240))
        return str(result.get('prompt') or '') if isinstance(result, dict) else ''

    def _task_provider(self, *provider_ids: Any) -> str:
        return next((_single_line(item, 160) for item in provider_ids if _single_line(item, 160)), '')

    def _llm_timer_timezone_name(self) -> str:
        return self.environment_perception_timezone

    def _llm_timer_run_at(self, scheduled_ts: float) -> datetime:
        return self._environment_fromtimestamp(scheduled_ts)

    async def _delete_official_llm_timer_job(self, job_id: str) -> tuple[bool, str]:
        api = self.v2_host_api()
        deleter = getattr(api, 'delete_reality_touch_cron_job', None) if api is not None else None
        if not callable(deleter):
            return (False, 'AstrBot 官方 Cron 不可用')
        result = await deleter(job_id)
        if isinstance(result, tuple):
            return (bool(result[0]), str(result[1] if len(result) > 1 else ''))
        return (bool(result), '')

    async def _reply(self, event: AstrMessageEvent, text: str) -> None:
        sender = getattr(event, 'send', None)
        if callable(sender):
            await sender(event.plain_result(str(text or '')))

    def _reality_touch_location_check_text(self, user_id: str) -> str:
        context = self.mobile_context(user_id)
        location = context.get('location') if isinstance(context.get('location'), dict) else {}
        if not context.get('available'):
            reason = _single_line(location.get('reason'), 80)
            if reason == 'mobile_gateway_disabled':
                return '位置检查：手机陪伴终端网关未启用或尚未运行。'
            ttl = _safe_int((context.get('privacy') or {}).get('expires_after_seconds') if isinstance(context.get('privacy'), dict) else 0, 900, 60)
            return f'位置检查：当前没有有效的手机前台位置；可能尚未上报、已撤销或已超过 {ttl} 秒有效期。'
        parts = ['位置检查：手机陪伴终端位置有效']
        place = location.get('place') if isinstance(location.get('place'), dict) else {}
        place_name = _single_line(place.get('name'), 40) if place.get('matched') else ''
        label = _single_line(location.get('label'), 40)
        if place_name:
            parts.append(f'标记地点：{place_name}')
        elif label:
            parts.append(f'设备标记：{label}')
        latitude = location.get('latitude')
        longitude = location.get('longitude')
        if latitude is not None and longitude is not None:
            parts.append(f'约略坐标：{latitude}, {longitude}')
        accuracy = _safe_float(location.get('accuracy_m'), 0.0, 0.0)
        if accuracy > 0:
            parts.append(f'精度约 {round(accuracy)} 米')
        captured_at = _safe_float(location.get('captured_at'), 0.0, 0.0)
        if captured_at > 0:
            try:
                timezone = zoneinfo.ZoneInfo(self.environment_perception_timezone or 'Asia/Shanghai')
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                timezone = zoneinfo.ZoneInfo('Asia/Shanghai')
            captured_text = datetime.fromtimestamp(captured_at, timezone).strftime('%Y-%m-%d %H:%M:%S')
            parts.append(f'采集时间：{captured_text}')
        age = _safe_int(location.get('age_seconds'), 0, 0)
        parts.append(f'距采集约 {age} 秒')
        return '；'.join(parts) + '。'
