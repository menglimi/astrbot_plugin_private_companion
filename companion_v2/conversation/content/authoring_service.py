"""Settings and version checks for native Content creation and continuation."""

from __future__ import annotations

from dataclasses import dataclass

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import TaskError

from . import authoring


GROUP = "content.authoring"
OWNER = "configuration"
AUTHORING_ACTIONS = authoring.AUTHORING_ACTIONS
# One configuration group has one owner, consumer, scope contract and apply boundary.
LAYERS = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE)

FIELDS = (
    FieldDefinition(GROUP + ".enabled", GROUP, GROUP, ValueKind.BOOLEAN, True,
                    layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER),
    FieldDefinition(GROUP + ".direction_prompt", GROUP, GROUP, ValueKind.TEXT, "",
                    layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER),
)

DEFAULTS = {field.field_id.rsplit(".", 1)[-1]: field.default for field in FIELDS}


def configuration_fields():
    return FIELDS


def target_for(context):
    """Resolve this role's authoring settings; never a window-scoped override."""
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role,
                        bot_id=context.scope.bot_id)


def effective_settings(runtime, context):
    """Read authoring settings for this relationship, falling back to defaults."""
    snapshot = SparseConfigurationOwner(runtime).read(target_for(context), FIELDS)
    return AuthoringSettings.from_values(
        {key.rsplit(".", 1)[-1]: field.value for key, field in snapshot.fields.items()})


@dataclass(frozen=True)
class AuthoringSettings:
    enabled: bool
    direction_prompt: str

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise TaskError("content_authoring_invalid", "the authoring switch must be boolean")
        if type(self.direction_prompt) is not str:
            raise TaskError("content_authoring_invalid", "the direction prompt must be text")
    @classmethod
    def from_values(cls, values):
        return cls(**{name: values.get(name, default) for name, default in DEFAULTS.items()})


@dataclass(frozen=True)
class Continuation:
    """The version a continuation read and the candidate that answered it."""

    work_id: str
    base_version: str
    base_revision: int

    def document(self):
        return {"work_id": self.work_id, "base_version": self.base_version, "base_revision": self.base_revision}


def _fail(code, reason):
    raise TaskError(code, reason)


def prepare(*, action, brief, base_version=""):
    """Validate one authoring request before it reaches a model.

    A rejected request never spends a model call. Continuation always names the
    fixed version that the native Content read returned.
    """
    if action not in AUTHORING_ACTIONS:
        _fail("content_authoring_action", "unsupported authoring action")
    if type(brief) is not str or not brief.strip():
        _fail("content_authoring_brief", "an authoring request needs a brief")
    if action == "continue" and (type(base_version) is not str or not base_version.strip()):
        _fail("content_authoring_base_version", "continuing requires the fixed version it extends")
    return {"action": action, "brief": brief.strip(), "base_version": base_version.strip()}


def select_work_id(title, items):
    """Resolve only one exact Content title; never guess among similar works."""
    if type(title) is not str or not title.strip() or type(items) is not list:
        _fail("content_work_lookup_invalid", "a title and Content catalogue are required")
    title = title.strip()
    matches = {}
    for item in items:
        if type(item) is not dict:
            _fail("content_work_lookup_invalid", "Content returned an invalid work summary")
        if item.get("title") != title:
            continue
        work_id = item.get("work_id")
        if type(work_id) is not str or not work_id.strip():
            _fail("content_work_lookup_invalid", "Content returned a work without its identifier")
        matches[work_id] = item
    if not matches:
        _fail("content_work_not_found", "no readable Content work has this exact title")
    if len(matches) != 1:
        _fail("content_work_ambiguous", "multiple readable Content works have this exact title")
    return next(iter(matches))


def prompt(request, materials, *, settings, work_type="", scheduled=False):
    """Render one validated request, including this installation's direction."""
    direction = settings.direction_prompt.strip()
    brief = request["brief"] if not direction else f"{direction}\n{request['brief']}"
    return authoring.authoring_prompt(request["action"], brief, materials,
        base_version=request["base_version"], work_type=work_type, scheduled=scheduled)


def parse_draft(raw, *, action):
    """Parse one draft without imposing a separate character ceiling."""
    try:
        return authoring.parse_output(raw, action=action)
    except ValueError as error:
        raise TaskError("content_authoring_output",
                        "the draft does not satisfy its output contract") from error


def continuation(request, arguments):
    """The version a continuation save must reuse, or None for the other actions.

    ``content.work.read`` pinned a version before the model wrote anything. If a
    human revision landed in between, the draft belongs to the version it read and
    must not be saved on top of the newer head.
    """
    if request.get("action") != "continue":
        return None
    base = request.get("base_version") or ""
    if arguments.get("base_version") != base:
        _fail("content_authoring_stale_base",
              "a continuation must save against the version it read, not the current head")
    work_id, revision = arguments.get("work_id"), arguments.get("base_revision")
    if type(work_id) is not str or not work_id.strip() or type(revision) is not int or revision < 0:
        _fail("content_authoring_stale_base", "a continuation must name its work and base revision")
    return Continuation(work_id, base, revision)


__all__ = ["AUTHORING_ACTIONS", "AuthoringSettings", "Continuation", "DEFAULTS", "FIELDS", "GROUP", "OWNER",
           "configuration_fields", "continuation", "effective_settings", "select_work_id",
           "parse_draft", "prepare", "prompt", "target_for"]
