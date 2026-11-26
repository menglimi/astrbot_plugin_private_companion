"""Trusted identity and model-facing tools of the included QQ Zone extension.

QQ Zone ships inside V2 instead of a sibling plugin, so the package descriptor is
an in-repo constant rather than a declaration reported by another Star. The
catalog entry is still only a *catalog* entry: installation, role enablement and
per-conversation consent are recorded separately, and a real call re-checks all
of them plus the host credential and generation.
"""

from companion_v2.capabilities.models import (
    CapabilityAuthorizationState, CapabilityPackageDescriptor, FurnitureDescriptor,
)
from companion_v2.conversation.external_tools import ExtensionToolDescriptor
from companion_v2.execution.models import ProviderBinding, SideEffect, encode
from companion_v2.model_gateway.models import ToolSpec

from .topics import SCENES, TOPIC_SPECS


EXTENSION = "qzone"
PACKAGE_ID = "companion_v2.qzone"
PUBLISHER_ID = "astrbot_plugin_private_companion"
PACKAGE_VERSION = "1.0.0"
ARTIFACT_HASH = "included-qzone-1.0.0"
PROVIDER = "qzone.included"
GENERATION = "included-1"
DATASET_OWNER = "qzone.feed"

READ_ACTION = "qzone.feed.read"
LIKE_ACTION = "qzone.post.like"
COMMENT_ACTION = "qzone.post.comment"
PUBLISH_ACTION = "qzone.post.publish"
PUBLISH_CONVERSATION_CHANGED = "qzone_conversation_changed"
ALBUM_LIST_ACTION = "qzone.album.list"

CAPABILITIES = (READ_ACTION, LIKE_ACTION, COMMENT_ACTION, PUBLISH_ACTION)
ACTIONS = (READ_ACTION, LIKE_ACTION, COMMENT_ACTION, PUBLISH_ACTION, ALBUM_LIST_ACTION)
PUBLIC_ACTIONS = (LIKE_ACTION, COMMENT_ACTION, PUBLISH_ACTION)

# A scanned post stays actionable only inside the conversation that scanned it
# and only for a bounded window. The model never invents a post identity; it
# passes back the reference the scan handed it.
REFERENCE_TTL_MINUTES = 30
MAX_REFERENCES_PER_SCOPE = 60

FURNITURE = (FurnitureDescriptor(furniture_id="qzone-postbox", extension_id=EXTENSION,
                                 display_name="空间橱窗", panel_id="qzone"),)


def _schema(properties, required=()):
    return encode({"type": "object", "additionalProperties": False,
                   "properties": dict(properties), "required": list(required)})


def package_descriptor():
    return CapabilityPackageDescriptor(extension_id=EXTENSION, package_id=PACKAGE_ID,
        publisher_id=PUBLISHER_ID, package_version=PACKAGE_VERSION, artifact_hash=ARTIFACT_HASH,
        capabilities=CAPABILITIES, dataset_owners=(DATASET_OWNER,), furniture=FURNITURE)


def has_publish_consent_in_other_conversation(repository, context):
    """Recognize a prior choice on the same private route for diagnostics."""
    previous = repository.latest_route_authorization(
        extension_id=EXTENSION, capability_id=PUBLISH_ACTION,
        subject=context.actor.subject, scope=context.scope,
    )
    return (previous is not None
            and previous.state is CapabilityAuthorizationState.ACTIVE
            and previous.scope != context.scope)


def _binding(action):
    return ProviderBinding(capability=action, provider=PROVIDER, version=PACKAGE_VERSION, generation=GENERATION)


def _descriptor(action, description, parameters, side_effect, timeout_seconds):
    return ExtensionToolDescriptor(tool=ToolSpec(action, description, parameters),
        package=package_descriptor(), binding=_binding(action), source_owner=DATASET_OWNER,
        side_effect=side_effect, timeout_seconds=timeout_seconds)


def _topic_catalogue():
    """The catalogue as the model reads it: judgement first, then the fitting scenes."""
    lines = ["发布前必须先定主题与场景：主题回答“这条在讲什么”，场景回答“为什么现在值得发”。",
             "主题（topic 只能取其中之一）："]
    for item in TOPIC_SPECS:
        scenes = "、".join(scene.value for scene in item.scenes)
        lines.append(f"- {item.topic.value}（{item.label}）：{item.guidance}可用场景：{scenes}。")
    lines.append("场景（scene 必须属于所选主题的可用场景）：" + "；".join(
        f"{key}＝{value}" for key, value in SCENES.items()))
    lines.append("confidence 是你对这次主题判断的把握（0 到 1）。"
                 "材料不足、无法去掉私密细节或事实不确定时不要调用本工具。"
                 "求助类可以带一个待确认的日程提案，但不能声称已经修改日程。")
    lines.append("配图请用当前相册真实返回的 image_id（可先调用 qzone.album.list 查看，含生成图和每日穿搭），"
                 "或先调用生图/改图工具生成同场景图片并把返回的 artifact_id（imgart_*）填入 image_ids；"
                 "文案与配图要围绕同一个事件，不要编造图片标识，source_refs 只是文字材料来源，不会自动变成配图。"
                 "基于聊天内容发说说时，提炼感受，默认省略对方身份、私聊原话及其他敏感细节。"
                 "只有回执 verified=true 且 images_verified=true 才能说配图已发布；"
                 "accepted 或结果未知时先读空间核对，不要重发。")
    return "\n".join(lines)


def _agenda_proposal_schema():
    return {"type": "object", "additionalProperties": False,
            "description": "仅求助/互动＋日程场景可填；这是待确认提案，不代表日程已被修改。",
            "properties": {"agenda_id": {"type": "string", "description": "材料里的日程标识。"},
                           "expected_revision": {"type": "integer", "description": "材料里读到的日程版本。"},
                           "intent": {"type": "string", "description": "希望如何调整或处理。"},
                           "source_refs": {"type": "array", "items": {"type": "string"},
                                           "description": "材料中已有的引用。"}},
            "required": ["agenda_id", "expected_revision", "intent"]}


def tool_descriptors():
    """The four基础动作; none of them sends a QQ message through the V2 sender."""

    return (
        _descriptor(READ_ACTION,
            "查看 QQ 空间动态：返回自己与好友最近说说的有界摘要。每条动态带一个只在本次对话内有效的引用，"
            "点赞或回复必须原样使用该引用，不能自己编造动态标识。",
            _schema({"scope": {"type": "string", "enum": ["own", "friends", "owner"],
                                "description": "读取自己的动态、好友动态或主人动态，省略则读取好友动态。"},
                     "limit": {"type": "integer", "description": "本次读取的条数上限，省略则用当前设置。"},
                     "detail": {"type": "boolean", "description": "是否一并读取有界评论摘要。"}}),
            SideEffect.NONE, 20),
        _descriptor(LIKE_ACTION,
            "给本次对话中刚查看过的一条 QQ 空间动态点赞。必须提供该动态的引用；已经点过赞的动态不会重复点赞。",
            _schema({"post_ref": {"type": "string", "description": "查看动态时返回的动态引用。"}}, ("post_ref",)),
            SideEffect.EXTERNAL_NETWORK, 20),
        _descriptor(COMMENT_ACTION,
            "回复一条本次对话中刚查看过的 QQ 空间动态。必须提供该动态的引用和回复正文；"
            "回复是公开动作，未确认结果不会重发。",
            _schema({"post_ref": {"type": "string", "description": "查看动态时返回的动态引用。"},
                     "text": {"type": "string", "description": "要公开发布的回复正文。"}}, ("post_ref", "text")),
            SideEffect.EXTERNAL_NETWORK, 30),
        _descriptor(PUBLISH_ACTION,
            "在自己的 QQ 空间发布一条说说。必须先按目录选定主题与场景，再说正文；"
            "发布是公开动作，未确认结果不会重发，并且受主题级每日上限、冷却与最小间隔约束；"
            "主题是否放行、置信度门槛和是否需要人工确认都由设置决定，被拒时按返回的原因处理，不要反复重试。\n"
            + _topic_catalogue(),
            _schema({"text": {"type": "string", "description": "要说说的正文。"},
                     "topic": {"type": "string", "enum": [item.topic.value for item in TOPIC_SPECS],
                               "description": "这条动态的主题。"},
                     "scene": {"type": "string", "enum": sorted(SCENES),
                               "description": "为什么现在值得发；必须属于所选主题的可用场景。"},
                     "confidence": {"type": "number", "description": "主题判断的把握，0 到 1。"},
                     "source_refs": {"type": "array", "items": {"type": "string"},
                                     "description": "本次正文实际用到的材料引用（来自材料清单里的 handle）。"},
                     "image_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "选自当前角色相册的真实 image_id（可通过 qzone.album.list 获取）或本轮生图返回的 artifact_id（imgart_*）；会实际上传并附到说说，最多 9 张。"},
                     "agenda_proposal": _agenda_proposal_schema()},
                    ("text", "topic", "scene", "confidence")),
            SideEffect.EXTERNAL_NETWORK, 300),
        ExtensionToolDescriptor(
            tool=ToolSpec(
                ALBUM_LIST_ACTION,
                "查看当前角色相册里近期可用的图片清单（含生成图和每日穿搭），返回真实 image_id 与摘要，供 qzone.post.publish 的 image_ids 配图使用。",
                _schema({"limit": {"type": "integer", "description": "最多返回多少张近期相册图片，默认 8，上限 24。"}}),
            ),
            package=package_descriptor(),
            binding=_binding(PUBLISH_ACTION),
            source_owner=DATASET_OWNER,
            side_effect=SideEffect.NONE,
            timeout_seconds=15,
        ),
    )


__all__ = ["ACTIONS", "ALBUM_LIST_ACTION", "ARTIFACT_HASH", "CAPABILITIES", "COMMENT_ACTION", "DATASET_OWNER",
           "EXTENSION", "FURNITURE", "GENERATION", "LIKE_ACTION", "MAX_REFERENCES_PER_SCOPE", "PACKAGE_ID",
           "PACKAGE_VERSION", "PROVIDER", "PUBLIC_ACTIONS", "PUBLISHER_ID", "PUBLISH_ACTION",
           "READ_ACTION", "REFERENCE_TTL_MINUTES", "package_descriptor", "tool_descriptors"]
