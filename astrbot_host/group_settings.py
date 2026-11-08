"""Explicit group targets; a group is never a private participant."""

from dataclasses import dataclass

from companion_v2.adapters.astrbot.native import HostAccount, account_number, route_id
from .settings import HostControlError, PROFILE_PATTERN, _text


@dataclass(frozen=True)
class GroupTarget:
    target_id: str
    platform_id: str
    account_id: str
    bot_id: str
    group_id: str

    @property
    def route_key(self):
        return self.platform_id, self.account_id, self.group_id


def group_session_matches(session_id, group_id):
    """Accept one shared group id or a legacy OneBot ``sender_group`` id."""
    if type(session_id) is not str or type(group_id) is not str or not session_id or not group_id:
        return False
    if session_id == group_id:
        return True
    parts = session_id.split("_")
    if len(parts) != 2 or parts[-1] != group_id:
        return False
    try:
        return all(account_number(part) == part for part in parts)
    except ValueError:
        return False


def parse_group_targets(values):
    fields = ("target_id", "platform_id", "account_id", "bot_id", "group_id")
    if type(values) is not list:
        raise HostControlError("host_mappings_invalid", "group targets must be a list")
    targets, routes = [], set()
    for value in values:
        if type(value) is not dict or set(value) - set(fields) - {"__template_key"} or set(fields) - set(value):
            raise HostControlError("host_mappings_invalid", "group target fields are incomplete or unknown")
        target = GroupTarget(**{field: _text(value[field], required=True) for field in fields})
        try:
            HostAccount(target.platform_id, target.account_id, target.bot_id)
            if target.group_id != "*":
                route_id(target.group_id)
                # OneBot 群号是十进制数字，必须去掉前导零；QQ 官方 openid 是
                # 不透明字符串，交给 route_id 校验即可。
                if (target.group_id.isdecimal()
                        and (not target.group_id.isascii()
                             or str(int(target.group_id)) != target.group_id)):
                    raise ValueError()
            if not PROFILE_PATTERN.fullmatch(target.target_id) or target.route_key in routes:
                raise ValueError()
        except ValueError:
            raise HostControlError("host_mappings_conflict", "group routes must be canonical and unique") from None
        routes.add(target.route_key)
        targets.append(target)
    return tuple(sorted(targets, key=lambda t: t.target_id))
