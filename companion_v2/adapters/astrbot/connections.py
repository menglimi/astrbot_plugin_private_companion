"""Replaceable native connections owned by stable configured Bot accounts.

Refreshing and lease checks share a short lock across event-loop and worker
threads. Host awaits and external sends must keep and recheck their lease.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock

from companion_v2.execution.models import TaskError


@dataclass(frozen=True)
class NativeConnectionLease:
    account_key: tuple[str, str]
    generation: int
    platform: object
    client: object


@dataclass
class _Connection:
    account: object
    generation: int = 0
    platform: object = None
    client: object = None
    transport: object = None
    identity: tuple | None = None
    platform_name: str = ""
    state: str = "waiting"
    reason_code: str = ""
    last_recovered_at: str | None = None
    waiters: set = field(default_factory=set)


def _identity(platform, account, name):
    """Use public account identifiers, never credentials, to permit rebinding."""
    if name == "aiocqhttp":
        return name, account.self_id
    if name == "wechat_native":
        value = getattr(getattr(platform, "native_config", None), "account_id", None)
    elif name == "telegram":
        # AstrBot rebuilds the bot object after polling errors. The numeric prefix of
        # a bot token is the bot's public user id, readable before it has connected.
        token = getattr(platform.get_client(), "token", None)
        value = token.partition(":")[0] if isinstance(token, str) and ":" in token else None
    else:
        value = getattr(platform, "appid", None) or getattr(platform, "app_id", None)
        if value is None:
            configuration = getattr(platform, "config", None)
            if isinstance(configuration, dict):
                value = configuration.get("appid") or configuration.get("app_id")
    return (name, str(value)) if value is not None and str(value) else None


def _transport(client, account_id):
    clients = getattr(client, "_wsr_api_clients", None)
    if isinstance(clients, dict):
        return next((value for key, value in clients.items() if str(key) == account_id), None)
    return client


class NativeAccountConnections:
    def __init__(self, context, accounts, *, now=None):
        self.context = context
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._accounts = {(account.platform_id, account.self_id): _Connection(account) for account in accounts}
        self.platforms = {}
        self.refresh()

    def _record(self, account):
        key = account if isinstance(account, tuple) else (account.platform_id, account.self_id)
        record = self._accounts.get(key)
        if record is None or (not isinstance(account, tuple) and account != record.account):
            raise TaskError("host_account_changed", "the configured native account projection changed")
        return record

    def generation(self, account):
        """Read the last observed generation without accepting a new connection."""
        with self._lock:
            return self._record(account).generation

    async def wait_changed(self, lease):
        waiter = asyncio.get_running_loop().create_future()
        with self._lock:
            record = self._record(lease.account_key)
            if record.generation != lease.generation:
                return
            record.waiters.add(waiter)
        try:
            await waiter
        finally:
            with self._lock:
                record.waiters.discard(waiter)

    @staticmethod
    def _notify(waiter):
        if not waiter.done():
            waiter.set_result(None)

    @staticmethod
    def _advance(record):
        record.generation += 1
        for waiter in record.waiters:
            loop = waiter.get_loop()
            if not loop.is_closed():
                loop.call_soon_threadsafe(NativeAccountConnections._notify, waiter)
        record.waiters.clear()

    def _unavailable(self, record, state, reason):
        if record.state != state or record.reason_code != reason:
            self._advance(record)
        record.state, record.reason_code = state, reason

    def _refresh(self, record):
        from .native import native_client_connected, supported_platform_name

        account = record.account
        platform = self.context.get_platform_inst(account.platform_id)
        if platform is None:
            if record.platform is not None and record.platform.meta().id != account.platform_id:
                self._unavailable(record, "identity_changed", "host_account_changed")
            else:
                self._unavailable(record, "waiting", "host_platform_unavailable")
            return
        meta = platform.meta()
        if not supported_platform_name(meta.name) or meta.id != account.platform_id:
            self._unavailable(record, "identity_changed", "host_platform_changed")
            return
        client = platform.get_client()
        identity = _identity(platform, account, meta.name)
        changed = record.platform is not None and (record.platform is not platform or record.client is not client)
        if (record.platform_name and record.platform_name != meta.name
                or record.identity is not None and identity != record.identity):
            self._unavailable(record, "identity_changed", "host_account_changed")
            return
        if changed and record.client is not None and record.identity is None:
            self._unavailable(record, "waiting", "host_account_identity_unavailable")
            return
        if record.identity is None:
            record.identity = identity
        connected = native_client_connected(platform, account.self_id, client)
        transport = _transport(client, account.self_id)
        replacement = (record.platform is not platform or record.client is not client
                       or record.transport is not transport)
        state, reason = ("online", "") if connected else ("waiting", "host_account_offline")
        recovered = connected and record.generation > 0 and (replacement or record.state != "online")
        if replacement or record.state != state or record.reason_code != reason:
            self._advance(record)
        record.platform, record.client, record.transport = platform, client, transport
        record.platform_name, record.state, record.reason_code = meta.name, state, reason
        if recovered:
            record.last_recovered_at = self.now().isoformat()
        self.platforms[account.platform_id] = platform, client

    def refresh(self, account=None):
        with self._lock:
            records = self._accounts.values() if account is None else (self._record(account),)
            for record in records:
                try:
                    self._refresh(record)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    self._unavailable(record, "error", "host_platform_probe_failed")
            return self.statuses()

    def statuses(self):
        with self._lock:
            return [{"platform_id": record.account.platform_id, "account_id": record.account.self_id,
                     "bot_id": record.account.bot_id, "platform_name": record.platform_name,
                     "state": record.state, "reason_code": record.reason_code,
                     "generation": record.generation, "last_recovered_at": record.last_recovered_at}
                    for record in self._accounts.values()]

    def online_platforms(self):
        """Return currently online ``(platform, client)`` pairs keyed by platform ID."""
        with self._lock:
            return {
                record.account.platform_id: (record.platform, record.client)
                for record in self._accounts.values()
                if record.state == "online" and record.platform is not None and record.client is not None
            }

    def acquire(self, account, *, connected=True):
        with self._lock:
            self.refresh(account)
            record = self._record(account)
            if record.state != "online" and (connected or record.reason_code != "host_account_offline"):
                raise TaskError(record.reason_code, "the configured account has no usable native connection")
            return NativeConnectionLease((record.account.platform_id, record.account.self_id),
                                         record.generation, record.platform, record.client)

    def assert_current(self, lease, *, connected=True):
        with self._lock:
            current = self.acquire(lease.account_key, connected=connected)
            if (current.generation != lease.generation or current.platform is not lease.platform
                    or current.client is not lease.client):
                raise TaskError("host_connection_changed", "the account connection changed during this operation")
            return current
