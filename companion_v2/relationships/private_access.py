"""Private-user access defaults: what a newcomer gets, and what a refusal sounds like.

Per-person permissions already exist in this product as **grants** —
``conversation.reply`` decides whether the companion may answer someone and
``proactive.contact`` whether it may reach out first (see
``docs/V2_USER_PAGE_GAP_PLAN_20260930.md``). This module therefore deliberately
adds **no second switch and no parallel store**: it owns only what a grant cannot
carry, namely the *defaults* a subject starts from and the line the companion
sends when answering is refused.

Two rules keep the two layers from fighting:

* a grant is the single source of truth for one person's permission — the page
  writes grants, never a copy of them;
* this group only says what an untouched person gets.

The refusal line must stay deliverable while ``conversation.reply`` is refused, so
nothing here touches ``text`` or ``message.delivery`` authority.
"""

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldDefinition, ValueKind


GROUP = "private_access"

#: 操作员可以**按人**收回的两项权限，以及它们落在授权层的 grant 能力名。
#: 这两项一旦被收回，宿主不能把"重新开通"当成修复手段——收回就是决定。
DIALOGUE_CAPABILITY = "conversation.reply"
PROACTIVE_CAPABILITY = "proactive.contact"
REVOCABLE_CAPABILITIES = frozenset({DIALOGUE_CAPABILITY, PROACTIVE_CAPABILITY})

#: 这两项按人权限各自对应配置里的哪个默认值：签发那一刻用它决定"新用户一开始有没有"。
DEFAULT_FOR_CAPABILITY = {DIALOGUE_CAPABILITY: "dialogue", PROACTIVE_CAPABILITY: "proactive"}

#: 撤回/过期/从未签发：这三种结果都表示"这个人现在没有这项权限"，
#: 而不是"宿主配置坏了"。授权层给出别的结论（例如另一张 grant 顶替）时仍然要报错。
REVOCABLE_ABSENCE_CODES = frozenset({"authorization_revoked", "authorization_expired",
                                     "authorization_denied", "scope_not_granted"})

#: The product default for a first private message: answer, but never reach out first.
DIALOGUE_DEFAULT = True
PROACTIVE_DEFAULT = False
DENIED_REPLY = "老大不让我和陌生人说话哦"
DENIED_REPLY_LIMIT = 200
#: One refusal line per subject per window by default; 0 sends it every time.
NOTICE_COOLDOWN_SECONDS = 21600
#: 陌生号（没有绑定、也不在任何目标里）也要被婉拒时，靠这两个值兜住频率与总量。
STRANGER_NOTICE_DEFAULT = True
STRANGER_NOTICE_DAILY_CAP = 3


def configuration_fields():
    """Defaults belong to installation → role: a role decides how it treats newcomers.

    No explicit ``owner_id``: like the ``person`` group, this group is written through
    the default sparse owner. Declaring an owner that the composition root does not
    register makes host start-up fail its contract check.
    """
    layers = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE)
    return (
        FieldDefinition("private_access.dialogue_default", GROUP, GROUP, ValueKind.BOOLEAN,
                        DIALOGUE_DEFAULT, layers=layers, apply_mode=ApplyMode.IMMEDIATE),
        FieldDefinition("private_access.proactive_default", GROUP, GROUP, ValueKind.BOOLEAN,
                        PROACTIVE_DEFAULT, layers=layers, apply_mode=ApplyMode.IMMEDIATE),
        FieldDefinition("private_access.denied_reply", GROUP, GROUP, ValueKind.TEXT,
                        DENIED_REPLY, layers=layers, apply_mode=ApplyMode.IMMEDIATE),
        FieldDefinition("private_access.notice_cooldown_seconds", GROUP, GROUP, ValueKind.INTEGER,
                        NOTICE_COOLDOWN_SECONDS, minimum=0, maximum=604800, layers=layers,
                        apply_mode=ApplyMode.IMMEDIATE),
        # 未绑定的陌生号正常情况下完全静默（事件留给 AstrBot 自己的管线）。打开这一项
        # 才会婉拒他们：一次固定文案、不建身份、不进记忆、不调用模型。
        FieldDefinition("private_access.stranger_notice_enabled", GROUP, GROUP, ValueKind.BOOLEAN,
                        STRANGER_NOTICE_DEFAULT, layers=layers, apply_mode=ApplyMode.IMMEDIATE),
        FieldDefinition("private_access.stranger_notice_daily_cap", GROUP, GROUP, ValueKind.INTEGER,
                        STRANGER_NOTICE_DAILY_CAP, minimum=0, maximum=100, layers=layers,
                        apply_mode=ApplyMode.IMMEDIATE),
    )


def target_for(context):
    """The role, not one conversation: a newcomer starts the same way in every window."""
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


class PrivateAccessSettings:
    """Reader for the group; the composition root attaches it where it is needed."""

    def __init__(self, configuration):
        from companion_v2.configuration.owners import SparseConfigurationOwner

        self.configuration = configuration
        self.owner = SparseConfigurationOwner(configuration.runtime)
        self.definitions = configuration.registry.group(GROUP)
        self.settings = self  # Alias so callers may hold either name.

    def read(self, context):
        with self.configuration.runtime.read_snapshot():
            snapshot = self.owner.read(target_for(context), self.definitions)
            return {key: item.value for key, item in snapshot.fields.items()}

    def read_installation(self, installation_id, *, role_targets=()):
        """Optionally fall back per field to one explicitly configured role.

        Multiple roles storing a field are ambiguous even if their values agree.
        Callers omitting role targets keep installation-only behavior.
        """
        target = ConfigTarget(installation_id, ConfigLayer.INSTALLATION)
        with self.configuration.runtime.read_snapshot():
            snapshot = self.owner.read(target, self.definitions)
            values = {key: item.value for key, item in snapshot.fields.items()}
            candidates = {key: [] for key, item in snapshot.fields.items() if not item.explicit}
            if not candidates:
                return values
            fields = tuple(field for field in self.definitions if field.field_id in candidates)
            for role_target in {item.key: item for item in role_targets}.values():
                if role_target.installation_id != installation_id or role_target.layer is not ConfigLayer.ROLE:
                    raise ValueError("private access fallback requires role targets in this installation")
                role = self.owner.read(role_target, fields)
                for key in candidates:
                    if role.fields[key].explicit:
                        candidates[key].append(role.fields[key].value)
            for key, items in candidates.items():
                if len(items) == 1:
                    values[key] = items[0]
            return values

    def defaults(self, context):
        """The starting point for a private user nobody has decided about."""
        return _defaults_of(self.read(context))

    def defaults_installation(self, installation_id, *, role_targets=()):
        return _defaults_of(self.read_installation(installation_id, role_targets=role_targets))


def _defaults_of(values):
    return {
        "dialogue": bool(values.get("private_access.dialogue_default")),
        "proactive": bool(values.get("private_access.proactive_default")),
        "denied_reply": str(values.get("private_access.denied_reply") or "").strip(),
        "notice_cooldown_seconds": int(values.get("private_access.notice_cooldown_seconds") or 0),
        "stranger_notice": bool(values.get("private_access.stranger_notice_enabled")),
        "stranger_daily_cap": int(values.get("private_access.stranger_notice_daily_cap") or 0),
    }
