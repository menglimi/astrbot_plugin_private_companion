"""Private, durable phone procedures learned from observed screen transitions."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
import unicodedata

from companion_v2.execution.models import digest, encode
from companion_v2.role_work.state.ownership import role_owner

from .checkout import checkout_boundary, checkout_visible, tap_position
from .session import PhoneAction


PREFIX = "phone.skill.v1:"
MAX_SKILLS = 64
MAX_TAPS = 40
FAILURE_PREFIX = "phone.skill.noop.v1:"
FAILURE_MAX_AGE = timedelta(days=7)
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
TAKEOUT_TERMS = ("美团", "外卖", "点餐", "点单", "奶茶", "饮品", "杨枝甘露", "沪上阿姨", "送餐", "夜宵", "点菜")
TAKEOUT_GUIDE = """手机技能「美团外卖点单」：美团已安装时可用 com.sankuai.meituan 启动。用户指定别的入口时沿用指定入口。
美团首页进入「外卖」。如果有活动弹窗，先关闭，再从外卖首页进入用户指定的商家，或从外卖搜索查找可配送的门店。
「请输入商品名」和「热门搜索」表示当前是店内商品搜索页，搜索对象是饮品或菜品。目标商品出现在热门或历史搜索时，直接点完整商品名，例如「杨枝甘露」。没有匹配词时才输入商品名，确认已显示在搜索框后再点「搜索」。输入框仍是占位词时，改用可见的商品入口或返回店铺菜单。
找到商品后点「选规格」。例如沪上阿姨有「杨枝甘露经典版」与「杨枝甘露清爽版」，「蔗糖换0卡糖」在「换糖」下，甜度单独选择。按本次要求核对「已选规格」，未指定规格可用合适的默认项。核对数量后加入购物车。若已经有符合目标的一杯，直接检查购物车，避免重复加入；若混有其他商品或未达到起送价，说明情况，由用户决定。
「去结算」用于打开订单确认页，必要时先收起规格弹窗。以结账页为准检查店铺、商品、数量、规格、收货地址和配送方式。
在美团结账页查看「美团红包」一栏。右侧只显示「5元红包」等面额时，点该行进入「可用红包」。已经显示「-¥5」等实际抵扣且优惠合适时保留。
选券前核对适用门店、满减门槛和叠加条件。优先按用户指定用券，否则选择让本单实付最低的可用红包。页面明确提供本单可免费领用的红包时可直接领用。保持本次商品、数量和配送要求，跳过「购神券包立减」、开通会员等付费促销。在红包页核对底部「红包优惠」并点「确认」应用。
回到结账页核对实际抵扣和最新实付。按结账协议停在支付前，amount 填当前实付，options 写上可核实的红包抵扣。用户要求用红包但没有可用红包时，在回执里说明，并遵循用户设定的用券条件。"""


def _label(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def takeout_guidance(goal):
    return TAKEOUT_GUIDE if any(term in goal for term in TAKEOUT_TERMS) else ""


@dataclass(frozen=True)
class SkillScreen:
    key: str
    targets: dict[str, tuple[int, int]]
    checkout: bool

    @classmethod
    def capture(cls, observation):
        if not observation.stable or not observation.screenshot:
            return None
        labels, targets, packages = [], {}, set()
        for element in observation.elements:
            if element.package == "com.android.systemui":
                continue
            packages.add(element.package)
            if not element.label:
                continue
            label = _label(element.label)
            anchor = digest([element.package, label])
            labels.append((anchor, element.class_name, element.enabled, element.clickable))
            x, y = element.center
            if element.enabled and 0 <= x < observation.width and 0 <= y < observation.height:
                targets.setdefault(anchor, set()).add(element.center)
        if not labels:
            return None
        unique = {anchor: next(iter(positions)) for anchor, positions in targets.items() if len(positions) == 1}
        return cls(digest([sorted(packages), list(dict.fromkeys(labels))]), unique, checkout_visible(observation))

    def tap(self, observation, action):
        """Retain an unambiguous label at the observed tap, including coordinate taps."""
        if self.checkout or action.kind not in {"tap", "tap_text"}:
            return None
        position = tap_position(observation, action)
        if position is None:
            return None
        anchors = [anchor for anchor, center in self.targets.items() if center == position]
        if len(anchors) != 1:
            return None
        return {"screen": self.key, "target": anchors[0]}


class PhoneSkillMemory:
    def __init__(self, runtime):
        self.runtime = runtime

    @staticmethod
    def _prefix(context):
        # Private sessions can expose different apps and accounts on the same device.
        return PREFIX + digest([role_owner(context), context.actor.subject]) + ":"

    def _key(self, context, goal, shopping):
        return self._prefix(context) + digest([_label(goal), shopping])

    def _taps(self, key):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
        if row is None:
            return []
        try:
            saved = json.loads(row["value"])
            return [tap for tap in saved["taps"] if _valid_tap(tap)][-MAX_TAPS:]
        except (ValueError, TypeError, KeyError):
            return []

    def recall(self, context, goal, shopping, screen, observation, history, *, avoided=()):
        if screen is None or screen.checkout:
            return None
        # Once the phone reports a tap at this screen, defer to the model if the
        # same screen appears again. This covers no-ops and intentional revisits.
        if any((item.get("skill_tap") or {}).get("screen") == screen.key for item in history):
            return None
        saved = self._taps(self._key(context, goal, shopping))
        for tap in reversed(saved):
            if tap["screen"] != screen.key or tap["target"] not in screen.targets or tap["target"] in avoided:
                continue
            x, y = screen.targets[tap["target"]]
            action = PhoneAction("tap", x=x, y=y, reason="复用已验证的手机操作")
            if not checkout_boundary(observation, action):
                return action
        return None

    def _recent_failures(self, context, at):
        key = FAILURE_PREFIX + context.key
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?", (key,))
        if row is None:
            return key, []
        try:
            items = json.loads(row["value"])
            return key, [item for item in items if _valid_tap(item["tap"]) and
                         at - FAILURE_MAX_AGE <= datetime.fromisoformat(item["at"]) <= at][-MAX_TAPS:]
        except (ValueError, TypeError, KeyError):
            return key, []

    def ineffective_targets(self, context, screen, at):
        if screen is None or screen.checkout:
            return ()
        _, failures = self._recent_failures(context, at)
        return tuple(item["tap"]["target"] for item in failures
                     if item["tap"]["screen"] == screen.key and item["tap"]["target"] in screen.targets)

    def observe_tap(self, context, step, at):
        tap = step.get("skill_tap")
        if not _valid_tap(tap):
            return
        with self.runtime.transaction() as connection:
            key, failures = self._recent_failures(context, at)
            remaining = [item for item in failures if item["tap"] != tap]
            if step.get("code") == "phone_action_ineffective":
                remaining.append({"tap": tap, "at": at.isoformat()})
            elif remaining == failures:
                return
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, encode(remaining[-MAX_TAPS:])))

    def remember(self, context, goal, shopping, history, at):
        verified = [item["skill_tap"] for item in history
                    if item.get("ok") and item.get("skill_verified") and _valid_tap(item.get("skill_tap"))]
        if not verified:
            return
        prefix = self._prefix(context)
        key = self._key(context, goal, shopping)
        with self.runtime.transaction() as connection:
            # Keep paths from alternative entry points. A newer verified tap
            # replaces the old one for the same screen.
            previous = {tap["screen"]: tap for tap in self._taps(key)}
            for tap in verified:
                previous.pop(tap["screen"], None)
                previous[tap["screen"]] = tap
            document = {"updated_at": at.isoformat(), "taps": list(previous.values())[-MAX_TAPS:]}
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, encode(document)))
            connection.execute("""DELETE FROM runtime_metadata WHERE key IN (
                SELECT key FROM runtime_metadata WHERE key >= ? AND key < ?
                ORDER BY json_extract(value, '$.updated_at') DESC, key LIMIT -1 OFFSET ?)""",
                (prefix, prefix + "\uffff", MAX_SKILLS))


def _valid_tap(tap):
    return (type(tap) is dict and tap.keys() == {"screen", "target"}
            and all(isinstance(value, str) and FINGERPRINT.fullmatch(value) for value in tap.values()))
