"""Server-only AstrBot balance connection binding; never serialize credentials."""
from copy import deepcopy
import os

from companion_v2.execution.models import digest


def _catalog(context):
    manager = getattr(context, "provider_manager", None)
    if manager is None:
        return [], [], ""
    sources = getattr(manager, "provider_sources_config", []) or []
    models = getattr(manager, "providers_config", []) or []
    # Prefer the current default configuration, not the last active chat session.
    getter = getattr(context, "get_config", None)
    config = getter() if callable(getter) else {}
    runner = config.get("agent_runner", {})
    default = (runner.get("config", {}).get("model", {}).get("provider_id", "")
               if runner.get("runner_type") == "local" else "")
    if not runner:
        default = config.get("provider_settings", {}).get("default_provider_id", "")
        default = default or getattr(manager, "default_chat_provider_id", "")
    entries = [s for s in sources if isinstance(s, dict)
               and s.get("provider_type", "chat_completion") == "chat_completion"]
    # Older AstrBot installations store a complete connection on the model itself.
    ids = {s.get("id") for s in entries}
    entries += [m for m in models if isinstance(m, dict) and not m.get("provider_source_id")
                and m.get("id") not in ids
                and m.get("provider_type", "chat_completion") == "chat_completion"]
    for model in models:
        if isinstance(model, dict) and model.get("id") == default:
            default = model.get("provider_source_id") or default
            break
    return entries, models, default


def balance_provider_choices(context):
    try:
        entries, _, default = _catalog(context)
    except Exception:
        return {"astrbot_providers": [], "astrbot_default_provider_id": ""}
    return {"astrbot_providers": [
        {"id": s["id"], "label": s["id"], "enabled": s.get("enable", True) is True}
        for s in entries if isinstance(s.get("id"), str) and s["id"]],
        "astrbot_default_provider_id": default if isinstance(default, str) else ""}


def provider_balance_settings(settings, context):
    selected = settings.get("advanced_provider_id", "")
    if type(selected) is not str or len(selected) > 256:
        raise ValueError("invalid balance provider selection")
    try:
        entries, _, default = _catalog(context)
    except Exception:
        raise ValueError("AstrBot balance provider configuration unavailable") from None
    identifier = selected or default
    source = next((s for s in entries if s.get("id") == identifier), None)
    if source is None or source.get("enable", True) is not True:
        raise ValueError("AstrBot balance provider is missing or disabled")
    keys = source.get("key", source.get("api_key", []))
    if isinstance(keys, str):
        keys = [keys]
    if not isinstance(keys, list):
        raise ValueError("AstrBot balance provider key list is invalid")
    key = ""
    for item in keys:
        if not isinstance(item, str):
            continue
        if item.startswith("$"):
            name = item[1:]
            if name.startswith("{") and name.endswith("}"):
                name = name[1:-1]
            item = os.environ.get(name, "")
        if item.strip():
            key = item
            break
    base = source.get("api_base", "")
    proxy = source.get("proxy", "")
    if not isinstance(base, str) or not base.strip() or not key or not isinstance(proxy, str):
        raise ValueError("AstrBot balance provider requires a base URL, API key and valid proxy")
    result = deepcopy(settings)
    result.update(advanced_base_url=base.strip(), advanced_api_key=key,
                  advanced_proxy_url=proxy.strip())
    return result


class AstrBotBalancePort:
    """Resolve credentials at each request, including after host config changes."""
    provider = "script"
    provider_label = "高级余额查询"
    currency_label = "额度"

    def __init__(self, settings, context):
        self.settings = deepcopy(settings)
        self.context = context
        self._port()  # Fail closed before save/test or initial assembly.

    def _port(self):
        from .service_balance_script import ScriptServiceBalancePort
        return ScriptServiceBalancePort(provider_balance_settings(self.settings, self.context))

    @property
    def source_fingerprint(self):
        try:
            return digest(["astrbot", self.settings.get("advanced_provider_id", ""),
                           self._port().source_fingerprint])
        except ValueError:
            return digest(["astrbot-unavailable", self.settings])

    async def fetch(self):
        port = self._port()
        fingerprint = port.source_fingerprint
        observation = await port.fetch()
        if self._port().source_fingerprint != fingerprint:
            raise ValueError("AstrBot balance provider changed during query")
        return observation
