"""第 1 轮配置/身份小修包的合同：空 provider 不遮蔽默认，多账号人格工坊可用。

两条审计发现，各自对应一半：

1. D5 / #309 —— V1 用「空字符串」表示「跟随默认」。通用规则转换会把这个空字符串
   当成显式值写进 ``model.*.provider``；而 V2 的 ``ModelRouter.provider_default``
   只在字段 ``source is None``（从未设置）时才套用宿主/任务档默认。于是迁移把一个
   「跟随默认」变成了显式空值，后台任务随后以 ``model_provider_unavailable`` 失败。
   未设置必须保持未设置；但这条语义只属于 ``model.*.provider``——其他字段的空值
   可能是有意的，不能被一起吞掉。
2. D6 / #311 —— ``MultiHostSession`` 根本没有 ``profile`` 属性，共享 provider 在
   ``settings.common.provider_id`` 上。人格工坊有三处直接读
   ``session.profile.provider_id``，所以多账号下「档案草稿」「问卷标准化」以及经
   ``_text_provider`` 的「试音」「风格规则」都在调用模型前就死了。

这里的 fixture 全部是合成数据：真实的任务档工厂（``authoring_profiles`` /
``planning_profile`` / ``persona_judge_profile``）给字段合同，真实的
``AstrBotMultiController`` 给多账号会话，真实的 ``ModelRouter`` 给路由语义。
不访问网络、不写生产数据。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

# AstrBot 在 import 时解析数据目录。先把它重定向到临时目录，免得跑测试在仓库里
# 建出 data/。与 tests/test_astrbot_staging_host.py 同一做法。
_previous_astrbot_root = os.environ.get("ASTRBOT_ROOT")
_astrbot_import_root = None
if _previous_astrbot_root is None:
    _astrbot_import_root = TemporaryDirectory(prefix="companion-v2-r1-import-")
    os.environ["ASTRBOT_ROOT"] = _astrbot_import_root.name

from astrbot_host.persona_workshop_api import PERSONA_TRYOUT_SCENARIOS, PersonaWorkshopWebAPI
from companion_v2.configuration.models import (ApplyMode, ConfigLayer, ConfigTarget,
    ConfigurationSnapshot, EffectiveValue)
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.foreground import ModelDraft
from companion_v2.execution.models import Lane, ProviderBinding, TaskError
from companion_v2.legacy.configuration_migration import convert_configuration, configuration_rules
from companion_v2.model_gateway.models import Amount, LocalProvider, ProviderResponse
from companion_v2.model_gateway.routing import ModelRouter, configuration_fields
from companion_v2.proactive.service import persona_judge_profile
from tests.multi_host_support import MultiFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE, SCOPE


def tearDownModule():
    if _astrbot_import_root is not None:
        os.environ.pop("ASTRBOT_ROOT", None)
        _astrbot_import_root.cleanup()


DRAFT_TARGET = "model.content.authoring.draft.provider"
JUDGE_TARGET = "model.proactive.judge.provider"


def rule(source, target, source_type="string", confidence="high"):
    return {"source": source, "source_type": source_type, "target": target, "confidence": confidence}


def registry_for(profiles):
    """The live field contract of these task profiles, as the gateway registers it."""
    return FieldRegistry(version="r1.config-identity", fields=configuration_fields(profiles))


def authoring_registry():
    from companion_v2.conversation.content.authoring_tasks import authoring_profiles
    profiles = authoring_profiles("task-default", draft_tokens=2048)
    return registry_for(profiles), DRAFT_TARGET


def judge_registry():
    return registry_for((persona_judge_profile("task-default", max_output_tokens=260),)), JUDGE_TARGET


def planning_registry():
    from companion_v2.agenda.planning import planning_profile
    profile = planning_profile("task-default", max_output_tokens=2048)
    return registry_for((profile,)), profile


class BlankProviderMigrationTests(unittest.TestCase):
    """D5: a V1 "follow the default" provider must stay unset, not become an empty value."""

    def test_blank_v1_providers_never_become_an_explicit_task_provider(self):
        # 四态：None、空串、单个空格、空白组合；外加「键根本不存在」。
        for value in (None, "", " ", "\t\n  "):
            with self.subTest(value=value):
                fields, target = authoring_registry()
                migration = convert_configuration(
                    {"model_assignment_config": {"CREATIVE_MODEL_PROVIDER_ID": value}},
                    registry=fields,
                )

                self.assertNotIn(target, migration.changes)
                self.assertNotIn(target, migration.sources)
                self.assertEqual(
                    [item for item in migration.differences if item.field_id == target], [],
                    "an unset provider is not an invalid value",
                )

    def test_a_missing_v1_provider_key_stays_unset_too(self):
        fields, target = authoring_registry()
        migration = convert_configuration({"model_assignment_config": {}}, registry=fields)

        self.assertNotIn(target, migration.changes)

    def test_a_blank_judge_provider_is_skipped_by_the_generic_rule_branch(self):
        # 主动评审 provider 在合并阶段另有校验；这里直接喂通用规则，确认通用分支
        # 本身也不会写出显式空值（两处都不能漏）。
        fields, target = judge_registry()
        rule_row = next(item for item in configuration_rules()
                        if item["source"] == "proactive_generation_config.PROACTIVE_PERSONA_JUDGE_PROVIDER_ID")
        migration = convert_configuration(
            {"proactive_generation_config": {"PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": "   "}},
            registry=fields, rules=(rule_row,),
        )

        self.assertNotIn(target, migration.changes)
        self.assertEqual(migration.differences, ())

    def test_a_non_blank_provider_still_migrates_with_its_source(self):
        # 非空值原样迁移：本轮只改「空值算未设置」，不顺手改写用户写下的 id
        # （前后空白是否该修剪是另一条语义，见报告里的残余风险）。
        for value in ("secondary", "  secondary  "):
            with self.subTest(value=value):
                fields, target = authoring_registry()
                migration = convert_configuration(
                    {"model_assignment_config": {"CREATIVE_MODEL_PROVIDER_ID": value}},
                    registry=fields,
                )

                self.assertEqual(migration.changes[target], value)
                self.assertEqual(migration.sources[target],
                                 ("model_assignment_config.CREATIVE_MODEL_PROVIDER_ID",))
                self.assertEqual(fields.fields[target].validate(migration.changes[target]), value)

    def test_blank_values_of_other_fields_still_migrate(self):
        # 修复范围只限 model.*.provider：其他字段的空值可能是有意的（用户清空
        # 自定义提示词），批量忽略会静默丢掉真实设置。
        fields, profile = planning_registry()
        provider_target, prompt_target = profile.group + ".provider", profile.group + ".prompt"
        migration = convert_configuration(
            {"legacy_compare": {"writer_provider": "", "task_prompt": ""}},
            registry=fields,
            rules=(rule("legacy_compare.writer_provider", provider_target),
                   rule("legacy_compare.task_prompt", prompt_target)),
        )

        self.assertNotIn(provider_target, migration.changes)
        self.assertEqual(migration.changes, {prompt_target: ""})


class ProviderDefaultRoutingTests(unittest.TestCase):
    """The same migration output through the real router: default vs. explicit provider."""

    TARGET = ConfigTarget(INSTALLATION, ConfigLayer.WINDOW, ROLE, SCOPE)

    def setUp(self):
        self.profile = persona_judge_profile("task-default", max_output_tokens=260)
        registry = registry_for((self.profile,))
        # 路由取值不读配置服务：构造期只用它的 registry 校验字段合同，所以给一个
        # 只带 registry 的替身就够，不必为一个纯路由断言建库。三个 provider 分别是
        # 任务档默认（fallback 的默认值）、宿主会话默认、以及用户手填的那个。
        self.router = ModelRouter(
            NS(registry=registry), (self.profile,),
            tuple(self.provider(name) for name in ("host-conversation", "handwritten", "task-default")),
            default_provider_resolver=lambda context, profile: "host-conversation",
        )

    @staticmethod
    def provider(name):
        async def generate(envelope):
            return ProviderResponse("generated", ModelDraft("draft", ()), Amount(1, 1), "receipt-" + name)

        return LocalProvider(ProviderBinding("model.text", name, "1", "local-" + name),
                             ("text",), 1024, lambda envelope: Amount(1, 1), generate)

    def snapshot(self, explicit=None):
        fields = {}
        for definition in configuration_fields((self.profile,)):
            explicit_value = (explicit or {}).get(definition.field_id)
            fields[definition.field_id] = (
                EffectiveValue(explicit_value, self.TARGET, True) if explicit_value is not None
                else EffectiveValue(definition.default, None, False)
            )
        return ConfigurationSnapshot(self.TARGET, self.profile.group, "r1.config-identity", 0, 0,
                                     "edit-token", "resolution-token", fields, "model_gateway",
                                     ApplyMode.NEXT_TASK)

    def routed(self, snapshot):
        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}
        # ``_route_selection`` is the guard from the audit (routing.py:116-122); the
        # full ``selected_route`` wrapper would need a live ConfigurationService.
        match, selected, _ = self.router._route_selection(
            self.profile, values, NS(scope=None),
            NS(context=NS(materials=()), event=NS()), NOW)
        return values, match, selected

    def mixed_snapshot(self, provider, fallback, *, provider_layer, fallback_layer):
        """A snapshot whose provider and fallback can come from different layers."""
        fields = {}
        for definition in configuration_fields((self.profile,)):
            suffix = definition.field_id.rsplit(".", 1)[-1]
            value, source = definition.default, None
            if suffix == "provider":
                value, source = provider, provider_layer
            elif suffix == "fallback":
                value, source = fallback, fallback_layer
            fields[definition.field_id] = (
                # ``explicit`` is true only for a value saved on the snapshot's
                # own target; inherited rows carry their parent layer instead.
                EffectiveValue(value, source, source is not None and source.layer is self.TARGET.layer)
                if source is not None
                else EffectiveValue(definition.default, None, False))
        return ConfigurationSnapshot(self.TARGET, self.profile.group, "r1.config-identity", 0, 0,
                                     "edit-token", "resolution-token", fields, "model_gateway",
                                     ApplyMode.NEXT_TASK)

    def test_a_stale_inherited_provider_clears_a_fallback_that_now_collides(self):
        # 继承的旧 Provider 已下线，回退成宿主模型后与显式保存的备用模型相同。
        # 不清掉备用会让每次调用都以 model_fallback_duplicate 失败。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "host-conversation",
            provider_layer=installation, fallback_layer=self.TARGET)

        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}
        fallback = applied.fields[self.profile.group + ".fallback"]

        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual(values["fallback"], "")
        # The saved fallback was explicit on this target; the repair must clear
        # that too, or resolve() would still raise model_fallback_duplicate.
        self.assertFalse(fallback.explicit)
        _, selected, _ = self.router._route_selection(
            self.profile, values, NS(scope=None),
            NS(context=NS(materials=()), event=NS()), NOW)
        self.assertEqual([item.binding.provider for item in selected], ["host-conversation"])

    def test_a_stale_saved_fallback_is_cleared_even_with_a_profile_default_fallback(self):
        # 任务档声明了默认备用模型时，旧的失效备用值也必须清掉；否则它留在
        # 路由链里，主模型正常也会因为解析不到 Provider 而拒绝调用。
        profile = replace(self.profile, default_fallback="task-default")
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("handwritten", "gone",
            provider_layer=self.TARGET, fallback_layer=installation)

        applied = self.router.apply_provider_default(
            profile, snapshot, self.router.provider_default(None, profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "handwritten")
        self.assertEqual(values["fallback"], "task-default")
        _, selected, _ = self.router._route_selection(
            profile, values, NS(scope=None),
            NS(context=NS(materials=()), event=NS()), NOW)
        self.assertEqual([item.binding.provider for item in selected],
                         ["handwritten", "task-default"])

    def test_an_explicitly_saved_stale_provider_still_fails_loudly(self):
        # 当前编辑层显式保存的值不在可回退范围：保存后 Provider 下线时，
        # 调用必须照旧报 model_provider_unavailable，而不是静默换模型。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "", provider_layer=self.TARGET,
                                       fallback_layer=installation)
        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}
        self.assertEqual(values["provider"], "gone")
        with self.assertRaises(TaskError):
            self.router._route_selection(self.profile, values, NS(scope=None),
                NS(context=NS(materials=()), event=NS()), NOW)

    def test_a_legacy_blank_on_the_installation_layer_is_recovered(self):
        # 旧版把“跟随默认”编码成空串写进安装层。安装层没有编辑入口，这个空串
        # 必须像失效 id 一样被回退，否则它会让每次调用失败、页面也无法保存。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("", "", provider_layer=installation,
                                       fallback_layer=installation)

        values, _, selected = self.routed(snapshot)

        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual([item.binding.provider for item in selected], ["host-conversation"])

    def test_an_unset_provider_keeps_the_host_conversation_default(self):
        values, match, selected = self.routed(self.snapshot())

        self.assertIsNone(match)
        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual(selected[0].binding.provider, "host-conversation")

    def test_an_explicit_non_blank_provider_still_wins_over_the_host_default(self):
        values, _, selected = self.routed(self.snapshot({self.profile.group + ".provider": "handwritten"}))

        self.assertEqual(values["provider"], "handwritten")
        self.assertEqual(selected[0].binding.provider, "handwritten")

    def test_writing_the_blank_provider_explicitly_is_what_broke_the_route(self):
        # 旧转换器的输出：显式空串 + 明确的来源。默认不再套用，provider 查不到。
        with self.assertRaises(TaskError) as caught:
            self.routed(self.snapshot({self.profile.group + ".provider": ""}))

        self.assertEqual(caught.exception.code, "model_provider_unavailable")

    def router_with(self, resolver, profile=None):
        profile = profile or self.profile
        return ModelRouter(
            NS(registry=registry_for((profile,))), (profile,),
            tuple(self.provider(name) for name in ("host-conversation", "handwritten", "task-default")),
            default_provider_resolver=resolver)

    def test_a_stale_fallback_does_not_hide_a_failing_host_route(self):
        # 主模型未设置时路线来自宿主会话；这条读取失败是真实配置错误。
        # 备用模型恰好是安装层的旧值，不能让它的可回退性把宿主的报错也吞掉。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("", "gone", provider_layer=None,
                                       fallback_layer=installation)

        def unavailable(context, profile):
            raise TaskError("model_provider_unavailable", "the session has no model")

        with self.assertRaises(TaskError) as caught:
            self.router_with(unavailable).provider_default(None, self.profile, snapshot)

        self.assertEqual(caught.exception.code, "model_provider_unavailable")

    def test_a_recovered_primary_collision_keeps_the_declared_fallback_default(self):
        # 主模型回退成宿主路线后与旧的备用值撞车。备用值本身也失效时，应回退到
        # 任务档声明的默认备用，而不是被清空、丢掉这份冗余。
        profile = replace(self.profile, default_fallback="task-default")
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "gone", provider_layer=installation,
                                       fallback_layer=installation)
        router = self.router_with(lambda context, item: "host-conversation", profile)

        applied = router.apply_provider_default(
            profile, snapshot, router.provider_default(None, profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual(values["fallback"], "task-default")
        _, selected, _ = router._route_selection(
            profile, values, NS(scope=None),
            NS(context=NS(materials=()), event=NS()), NOW)
        self.assertEqual([item.binding.provider for item in selected],
                         ["host-conversation", "task-default"])

    def test_an_explicit_fallback_colliding_with_a_recovered_primary_uses_the_task_default(self):
        # 主模型恢复成宿主路线后与显式备用撞车。备用本身可用，但相等会让每次
        # 调用以 duplicate 失败；任务档声明的默认备用是唯一不重复的选择。
        profile = replace(self.profile, default_fallback="task-default")
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "host-conversation", provider_layer=installation,
                                       fallback_layer=self.TARGET)
        router = self.router_with(lambda context, item: "host-conversation", profile)

        applied = router.apply_provider_default(
            profile, snapshot, router.provider_default(None, profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual(values["fallback"], "task-default")

    def test_a_recovered_value_drops_the_dead_rows_revision(self):
        # 回退值是运行时算出来的，不是那一行旧配置的内容；把它的 revision 一起
        # 带出去，会让按 revision 追踪来源的诊断指错行。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "", provider_layer=installation,
                                       fallback_layer=installation)
        key = self.profile.group + ".provider"
        snapshot = replace(snapshot, fields={
            **snapshot.fields,
            key: EffectiveValue("gone", installation, False, source_revision=7),
        })

        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))

        recovered = applied.fields[key]
        self.assertEqual(recovered.value, "host-conversation")
        self.assertIsNone(recovered.source)
        self.assertEqual(recovered.source_revision, 0)

    def test_a_declared_fallback_default_spares_the_host_route(self):
        # 任务档已声明默认备用时，失效的备用值可由它恢复，宿主路线用不上。
        # 此时去问宿主只会多出一次可能失败的读取，而恢复本身并不需要它。
        profile = replace(self.profile, default_fallback="task-default")
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("handwritten", "gone", provider_layer=self.TARGET,
                                       fallback_layer=installation)

        def unavailable(context, item):
            raise TaskError("model_provider_unavailable", "the session has no model")

        self.assertIsNone(
            self.router_with(unavailable, profile).provider_default(None, profile, snapshot))

    def test_a_recovered_fallback_is_not_left_equal_to_the_primary(self):
        # 主备是两个不同的失效 id 时，备用会按宿主路线恢复；若它与恢复后的
        # 主模型相同，就不该留在字段里冒充一条实际会被丢弃的备用链路。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("removed-a", "removed-b", provider_layer=installation,
                                       fallback_layer=installation)

        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual(values["fallback"], "")

    def test_a_settled_primary_survives_a_failing_host_route(self):
        # 本层已显式选好主模型时，宿主路线只可能用于恢复失效的备用模型。
        # 读不到宿主不该让整次调度失败：备用回退到任务档默认即可。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("handwritten", "gone", provider_layer=self.TARGET,
                                       fallback_layer=installation)

        def unavailable(context, item):
            raise TaskError("model_provider_unavailable", "the session has no model")

        router = self.router_with(unavailable)
        applied = router.apply_provider_default(
            self.profile, snapshot, router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "handwritten")
        self.assertEqual(values["fallback"], "task-default")

    def test_a_blank_installation_fallback_stays_no_fallback(self):
        # 空备用是「不使用备用模型」的合法选择，不是需要修复的旧值。把它恢复成
        # 宿主路线会给原本没有备用的任务凭空造出一条故障转移。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("handwritten", "", provider_layer=installation,
                                       fallback_layer=installation)

        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["fallback"], "")

    def test_a_recovered_primary_does_not_duplicate_an_unset_fallback(self):
        # 主模型从失效值恢复成宿主路线时，未设置的备用不该被填成同一个值：
        # 页面会显示一条实际被路由当作重复丢弃的备用链路。
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "", provider_layer=installation,
                                       fallback_layer=None)

        applied = self.router.apply_provider_default(
            self.profile, snapshot, self.router.provider_default(None, self.profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "host-conversation")
        self.assertEqual(values["fallback"], "")

    def test_a_host_route_that_cannot_serve_the_task_yields_the_task_default(self):
        # 安装层主模型失效、宿主模型是纯文本，而任务需要图片：恢复成宿主只会让
        # 每次调用以能力不符失败，任务档默认才是可用的那条路由。
        profile = replace(self.profile, capabilities=("text", "image"))
        router = ModelRouter(
            NS(registry=registry_for((profile,))), (profile,),
            tuple(self.provider(name) for name in
                  ("host-conversation", "handwritten", "task-default")),
            default_provider_resolver=lambda context, item: "host-conversation")
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("gone", "", provider_layer=installation,
                                       fallback_layer=installation)

        applied = router.apply_provider_default(
            profile, snapshot, router.provider_default(None, profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "task-default")

    def test_a_provider_that_cannot_serve_the_task_counts_as_stale(self):
        # 安装层留下一个存在但不支持该任务能力的 Provider，和已下线的 id 一样
        # 会让每次调用失败；安装层没有编辑入口，所以同样必须被回退。
        profile = replace(self.profile, capabilities=("text", "image"))
        host = replace(self.provider("host-conversation"), capabilities=("text", "image"))
        router = ModelRouter(
            NS(registry=registry_for((profile,))), (profile,),
            (host, self.provider("handwritten"), self.provider("task-default")),
            default_provider_resolver=lambda context, item: "host-conversation")
        installation = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        snapshot = self.mixed_snapshot("handwritten", "", provider_layer=installation,
                                       fallback_layer=installation)

        applied = router.apply_provider_default(
            profile, snapshot, router.provider_default(None, profile, snapshot))
        values = {name.rsplit(".", 1)[-1]: item.value for name, item in applied.fields.items()}

        self.assertEqual(values["provider"], "host-conversation")


class SessionProviderIdTests(unittest.TestCase):
    """D6: how the workshop finds the provider a host session is actually bound to."""

    def test_a_multi_host_session_reports_the_shared_provider(self):
        session = NS(settings=NS(common=NS(provider_id="shared-model")),
                     model=NS(provider_id="shared-model"))

        self.assertFalse(hasattr(session, "profile"))
        self.assertEqual(PersonaWorkshopWebAPI._session_provider_id(session), "shared-model")

    def test_a_single_host_session_keeps_reading_its_pinned_profile(self):
        session = NS(profile=NS(provider_id="pinned-model"), model=NS(provider_id="pinned-model"))

        self.assertFalse(hasattr(session, "settings"))
        self.assertEqual(PersonaWorkshopWebAPI._session_provider_id(session), "pinned-model")

    def test_a_blank_shared_provider_does_not_hide_a_pinned_profile(self):
        session = NS(settings=NS(common=NS(provider_id="   ")), profile=NS(provider_id="pinned-model"))

        self.assertEqual(PersonaWorkshopWebAPI._session_provider_id(session), "pinned-model")

    def test_a_session_without_any_provider_shape_reports_nothing_instead_of_raising(self):
        # 旧实现在这里抛 AttributeError，并被工坊的兜底 except 压成通用失败。
        session = NS(model=NS(provider_id="whatever"))

        self.assertEqual(PersonaWorkshopWebAPI._session_provider_id(session), "")

    def test_only_a_text_provider_counts_as_configured(self):
        for candidate in (None, 42, "", "  "):
            with self.subTest(provider_id=candidate):
                session = NS(settings=NS(common=NS(provider_id=candidate)),
                             profile=NS(provider_id=candidate))

                self.assertEqual(PersonaWorkshopWebAPI._session_provider_id(session), "")

    def test_no_provider_fails_with_a_named_error_instead_of_an_attribute_error(self):
        session = NS(settings=NS(common=NS(provider_id="")),
                     model=NS(provider_id="native-model", native=NS(text_chat=lambda **_: None)))
        controller = NS(controller=NS(session=session, last_session=None))

        with self.assertRaises(TaskError) as caught:
            PersonaWorkshopWebAPI._text_provider(controller)

        self.assertEqual(caught.exception.code, "role_profile_model_unavailable")
        self.assertIn("provider", str(caught.exception))

    def test_a_model_bound_to_another_provider_is_still_refused(self):
        session = NS(settings=NS(common=NS(provider_id="shared-model")),
                     model=NS(provider_id="other-model", native=NS(text_chat=lambda **_: None)))
        controller = NS(controller=NS(session=session, last_session=None))

        with self.assertRaises(TaskError) as caught:
            PersonaWorkshopWebAPI._text_provider(controller)

        self.assertEqual(caught.exception.code, "role_profile_model_unavailable")

    def test_the_selected_text_provider_is_returned_for_a_multi_host_session(self):
        provider = NS(text_chat=AsyncMock())
        session = NS(settings=NS(common=NS(provider_id="shared-model")),
                     model=NS(provider_id="shared-model", native=provider,
                              _current=lambda: None),
                     application=object())
        controller = NS(controller=NS(session=session, last_session=None))

        selected, application = PersonaWorkshopWebAPI._text_provider(controller)

        self.assertIs(selected, provider)
        self.assertIs(application, session.application)


class Query(dict):
    """The host's query object: a mapping that also reports repeated fields."""

    def getlist(self, key):
        return [self[key]] if key in self else []


class MultiAccountPersonaWorkshopTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    """D6 end to end: two Bot accounts, two personas, one shared provider."""

    async def asyncSetUp(self):
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        self.controller.register_routes()
        self.routes = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        # bot-b 的窗口人格指向另一个人格：如果两个目标的来源串了，请求正文里
        # 直接就能看出来。
        self.native.selections["qq-second"] = "second"
        self.window_personas["qq-second:FriendMessage:200"] = "second"

    def persona_path(self, target_id, role_id):
        return next(role["persona_api"]
                    for target in self.controller.web_api.target_inventory()
                    if target["target_id"] == target_id
                    for role in target["roles"] if role["role_id"] == role_id)

    async def post(self, suffix, body, *, target_id="first", role_id="role-a"):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

        async def reader():
            return payload

        with patch("astrbot_host.persona_workshop_api.request",
                   NS(username="dashboard-admin", query=Query(), body=reader)):
            return await self.routes[self.persona_path(target_id, role_id) + suffix]()

    def assert_source_persona(self, prompt, expected, unexpected):
        self.assertIn(expected, prompt)
        self.assertNotIn(unexpected, prompt)

    async def test_profile_draft_works_for_every_target_on_the_shared_provider(self):
        response_text = json.dumps({"fields": {"name": "旅人"}, "inferred_fields": [], "notes": []},
                                   ensure_ascii=False)
        provider = self.controller.session.model.native
        with patch.object(provider, "text_chat",
                          AsyncMock(return_value=NS(completion_text=response_text))) as call:
            first = await self.post("/profile-draft", {"supplement": ""})
            second = await self.post("/profile-draft", {"supplement": ""},
                                     target_id="second", role_id="role-b")

        self.assertEqual(first["status"], "ok", first)
        self.assertEqual(second["status"], "ok", second)
        self.assertEqual(first["data"]["source"]["name"], "original")
        self.assertEqual(second["data"]["source"]["name"], "second")
        first_prompt = call.await_args_list[0].kwargs["prompt"]
        second_prompt = call.await_args_list[1].kwargs["prompt"]
        self.assert_source_persona(first_prompt, "你是原来的角色", "你是第二个人格")
        self.assert_source_persona(second_prompt, "你是第二个人格", "你是原来的角色")
        # 共享 provider：会话模型就是 settings.common 选中的那个。
        self.assertEqual(self.controller.session.model.provider_id,
                         self.controller.settings.common.provider_id)
        # 读人格不许切换聊天当前人格。
        self.assertEqual(self.native.selections, {"qq-main": "original", "qq-second": "second"})

    async def test_standardize_uses_the_shared_provider_for_a_multi_account_target(self):
        result_text = "# 人格设定\n\n身份：星港领航员。"
        generated = json.dumps({"text": result_text, "notes": ["保留了谨慎的性格设定。"]},
                               ensure_ascii=False)
        provider = self.controller.session.model.native
        with patch.object(provider, "text_chat",
                          AsyncMock(return_value=NS(completion_text=generated))) as call:
            response = await self.post("/standardize", {
                "persona_id": "second", "answers": {"identity": "星港领航员", "speech": "简短清晰"},
            }, target_id="second", role_id="role-b")

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["draft"]["text"], result_text)
        self.assertEqual(response["data"]["source"]["persona_id"], "second")
        self.assertIn("星港领航员", call.await_args.kwargs["prompt"])
        self.assertIn("简短清晰", call.await_args.kwargs["prompt"])

    async def test_tryouts_and_style_rules_resolve_the_same_shared_provider(self):
        tryout_result = {"scenarios": [
            {"id": scene["id"], "options": [
                {"text": "嗯，我听着。", "tag": "贴合"},
                {"text": "让我想一想。", "tag": "另一种表达"},
                {"text": "请问您需要我提供什么帮助？", "tag": "对照"},
            ]} for scene in PERSONA_TRYOUT_SCENARIOS
        ]}
        provider = self.controller.session.model.native
        with patch.object(provider, "text_chat", AsyncMock(
                return_value=NS(completion_text=json.dumps(tryout_result, ensure_ascii=False)))) as call:
            tryouts = await self.post("/tryouts", {
                "persona_id": "original", "draft_text": "角色谨慎，回答简短。",
            })

        self.assertEqual(tryouts["status"], "ok", tryouts)
        self.assertEqual(len(tryouts["data"]["scenarios"]), len(PERSONA_TRYOUT_SCENARIOS))
        self.assertIn("角色谨慎", call.await_args.kwargs["prompt"])

        style_result = {"text": "短句回应，语气平稳。", "examples": "嗯，我听着。"}
        with patch.object(provider, "text_chat", AsyncMock(
                return_value=NS(completion_text=json.dumps(style_result, ensure_ascii=False)))) as call:
            style = await self.post("/style-rules", {
                "persona_id": "original", "draft_text": "角色谨慎，回答简短。",
                "selections": [{"scene_id": "everyday", "text": "嗯，我听着。"}],
                "speech_preference": "短句", "constraint_preference": "不写动作旁白",
            })

        self.assertEqual(style["status"], "ok", style)
        self.assertEqual(style["data"]["style"]["text"], style_result["text"])
        self.assertIn("不写动作旁白", call.await_args.kwargs["prompt"])

    async def test_a_target_without_a_model_provider_reports_a_diagnosable_error(self):
        # 共享 provider 为空时宿主自己会拒绝启动；这里只临时改会话对象，确认工坊
        # 给的是可诊断错误码，而不是被兜底 except 压成通用失败。
        with patch.object(self.controller.session, "settings", NS(common=NS(provider_id=""))):
            response = await self.post("/profile-draft", {"supplement": ""})

        self.assertEqual(response["status"], "error", response)
        self.assertEqual(response["data"]["code"], "role_profile_model_unavailable")


if __name__ == "__main__":
    unittest.main()
