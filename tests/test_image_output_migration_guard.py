"""生图输出迁移的重跑边界：V2 已有的设置不能被 V1 迁移再写一遍。

V1 迁移可以重复试算、重复执行，所以它必然落在「新版界面可能已经改过」的
``native_v2.output`` 上。几个方向都要钉住，缺一个就会出问题：

* V2 只在新版界面配了下载代理（还没有端点队列）时，V1 端点必须照常迁入；
* V2 已保存的下载代理、或已清空的下载代理（以及整份已迁入的配置），不能被
  第二遍迁移改写；
* 宿主侧的 ``_prepare_image_output_intent`` 必须把迁移层的保留判定落成差异记录、
  过滤掉不可写字段的 confidence/sources，并在没有可写组时不返回意图；
* ``_prepare_intents`` 必须把 ``image_output_config`` 意图交给上面那个方法处理，
  而不是让它掉到通用配置路径上。

保存走真实的 ``save_configuration``，因此断言的是落盘后的配置，而不是一份
手写的近似快照。宿主方法无法直接 import（``astrbot_host.legacy_api`` 在导入期
依赖 AstrBot 宿主 SDK），由 ``_host_type`` 从真实源码按方法名取出后编译执行。
"""

from __future__ import annotations

import ast
from dataclasses import replace
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from astrbot_host.native_extensions.image_native.output_configuration import (
    public_configuration,
    save_configuration,
)
from companion_v2.configuration.models import ConfigLayer
from companion_v2.execution.models import digest
from companion_v2.legacy.image_configuration_migration import (
    convert_image_configuration,
    plan_image_output_changes,
)
from companion_v2.legacy.migration import ConfigIntent, MigrationPlan


class _Config(dict):
    def __init__(self, value):
        super().__init__(value)
        self.saves = 0

    def save_config(self):
        self.saves += 1


class _Provider:
    def __init__(self):
        self.owner = SimpleNamespace(_tasks=[], backend=None)
        self.wait_budget_seconds = 0
        self._closed = False

    def task_timeout_seconds(self):
        return 300


def fresh_plugin(directory):
    """与 ImageConfig 给新装的默认值一致：还没有人在这台机器上迁过生图。"""
    return SimpleNamespace(
        data_dir=Path(directory),
        _native_v2_provider=_Provider(),
        config=_Config({"native_v2": {
            "enabled": True,
            "backend": "auto",
            "output": {"enabled": False, "backend": "auto", "endpoints": [],
                       "sd_webui": {}, "comfyui": {}},
        }}),
    )


def v1_photo_action_config():
    """一份只配了在线生图队列与下载代理的 V1 配置。"""
    return {"photo_action_config": {
        "photo_generation_backend": "external",
        "external_image_api_endpoints": [{"name": "Legacy", "platform": "openai",
            "base_url": "https://api.example.test/v1", "api_key": "legacy-key",
            "model": "image-v1"}],
        "external_image_download_proxy": "http://v1-proxy:8080",
    }}


_HOST_SOURCE = Path(__file__).resolve().parents[1] / "astrbot_host" / "legacy_api.py"
# ``_prepare_intents`` 连同它的两个依赖一起取出：只测预处理方法本身，无法发现
# 分发分支被改名后生图意图掉到通用配置路径上的情况。
_HOST_METHODS = ("_image_extension_api", "_prepare_image_output_intent", "_intent_key",
                 "_prepare_intents", "_configuration_layer_intents", "_scope_target")


def _method_lines(node, lines):
    first = min([node.lineno] + [item.lineno for item in node.decorator_list])
    return lines[first - 1:node.end_lineno]


def _host_type():
    """由真实源码拼出只含生图输出预处理链的宿主替身。

    被取出的方法本身不依赖 AstrBot SDK；方法改名或删除会让这里直接失败，
    而不会悄悄退回一份手写副本。唯一替换掉的是 ``_operator``（导入期需要
    ``astrbot.api.web``），生图分支根本不会用到它。
    """
    source = _HOST_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_HOST_SOURCE))
    owner = next((node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "LegacyMigrationWebAPI"), None)
    if owner is None:
        raise AssertionError("legacy_api 中找不到 LegacyMigrationWebAPI")
    methods = {node.name: node for node in owner.body if isinstance(node, ast.FunctionDef)}
    missing = [name for name in _HOST_METHODS if name not in methods]
    if missing:
        raise AssertionError(f"legacy_api 缺少被测方法：{missing}")
    lines = source.splitlines()
    body = "\n".join("\n".join(_method_lines(methods[name], lines)) for name in _HOST_METHODS)
    namespace = {"replace": replace, "plan_image_output_changes": plan_image_output_changes,
                 "digest": digest, "ConfigLayer": ConfigLayer}
    exec(compile("class _ExtractedHost:\n" + body, str(_HOST_SOURCE), "exec"), namespace)
    extracted = namespace["_ExtractedHost"]
    extracted._operator = lambda self: "operator"
    return extracted


class ImageOutputMigrationRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.plugin = fresh_plugin(temporary.name)

    def migrated(self):
        return convert_image_configuration(v1_photo_action_config())

    def settings(self):
        return public_configuration(self.plugin)["settings"]

    def stored(self):
        return self.plugin.config["native_v2"]["output"]

    def save(self, changes):
        return save_configuration(self.plugin, public_configuration(self.plugin)["revision"], changes)

    def test_empty_v2_output_receives_every_migrated_group(self):
        migrated = self.migrated()

        changes, differences = plan_image_output_changes(self.settings(), migrated.output_changes)

        self.assertEqual(set(changes), set(migrated.output_changes))
        self.assertEqual(differences, ())

    def test_v2_download_proxy_alone_does_not_block_the_endpoint_migration(self):
        self.save({"network": {"download_proxy": "http://v2-proxy:3128"}})

        migrated = self.migrated()
        changes, differences = plan_image_output_changes(self.settings(), migrated.output_changes)

        self.assertEqual(set(changes), set(migrated.output_changes) - {"network"})
        self.assertIn("endpoints", changes)
        self.assertEqual([item["kind"] for item in differences], ["preserved_v2"])
        self.assertEqual([item["field"] for item in differences], ["image.output.network"])

    def test_saved_v2_download_proxy_survives_the_endpoint_migration(self):
        self.save({"network": {"download_proxy": "http://v2-proxy:3128"}})

        changes, _ = plan_image_output_changes(self.settings(), self.migrated().output_changes)
        self.save(changes)

        self.assertEqual(self.stored()["network"]["download_proxy"], "http://v2-proxy:3128")
        self.assertEqual([item["base_url"] for item in self.stored()["endpoints"]],
                         ["https://api.example.test/v1"])

    def test_cleared_v2_download_proxy_is_not_restored_by_a_rerun(self):
        self.save({"network": {"download_proxy": "http://v2-proxy:3128"}})
        self.save({"network": {"clear_download_proxy": True}})
        # 已清空的组与从未改过的组都投影成 download_proxy_set=False；只有显式
        # 保存过才会带 use_environment_proxy。
        self.assertEqual(self.settings()["network"],
                         {"download_proxy_set": False, "use_environment_proxy": False})

        changes, differences = plan_image_output_changes(self.settings(), self.migrated().output_changes)

        self.assertNotIn("network", changes)
        self.assertIn("endpoints", changes)
        self.assertEqual([item["field"] for item in differences], ["image.output.network"])
        self.save(changes)
        self.assertEqual(self.stored()["network"],
                         {"download_proxy": "", "use_environment_proxy": False})
        self.assertEqual([item["base_url"] for item in self.stored()["endpoints"]],
                         ["https://api.example.test/v1"])

    def test_unrelated_v2_save_also_owns_the_network_group(self):
        # 已知取舍：保存端每次保存都会把 network 组整组落盘，因此「在 V2 保存过
        # 任何生图配置」与「显式清空过代理」在公开投影上不可区分。保存一个与网络
        # 无关的组之后，network 组同样带 use_environment_proxy，按键存在判定会把
        # 它当作 V2 已拥有，于是不再回填 V1 代理——保守方向的代价，这里钉住它。
        self.save({"legacy_options": {"foo": 1}})
        self.assertEqual(self.settings()["network"],
                         {"download_proxy_set": False, "use_environment_proxy": False})

        changes, differences = plan_image_output_changes(self.settings(), self.migrated().output_changes)

        self.assertNotIn("network", changes)
        self.assertIn("endpoints", changes)
        self.assertEqual([item["field"] for item in differences], ["image.output.network"])
        self.assertEqual([item["kind"] for item in differences], ["preserved_v2"])
        self.assertEqual(differences[0]["after"], "V2 已保存的在线图片下载代理设置")
        self.assertEqual(differences[0]["explain"],
                         "V2 已保存在线图片下载代理设置（含显式清空），为避免覆盖，"
                         "本次保留 V2 设置。其余 V1 图片设置照常迁移。")
        self.save(changes)
        self.assertEqual(self.stored()["network"],
                         {"download_proxy": "", "use_environment_proxy": False})
        self.assertEqual([item["base_url"] for item in self.stored()["endpoints"]],
                         ["https://api.example.test/v1"])

    def test_second_migration_over_a_populated_output_preserves_it_and_writes_nothing(self):
        first = self.migrated()
        self.save(first.output_changes)
        before = public_configuration(self.plugin)

        changes, differences = plan_image_output_changes(before["settings"], first.output_changes)

        self.assertEqual(changes, {})
        self.assertEqual([item["kind"] for item in differences], ["preserved_v2"])
        self.assertEqual(before["revision"], public_configuration(self.plugin)["revision"])
        self.assertEqual(self.plugin.config.saves, 1)


class ImageOutputHostPrepareTests(unittest.TestCase):
    """宿主侧 ``_prepare_image_output_intent`` 的职责，走真实类方法源码。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.plugin = fresh_plugin(temporary.name)
        self.host_type = _host_type()

    def save(self, changes):
        return save_configuration(self.plugin, public_configuration(self.plugin)["revision"], changes)

    def intent(self):
        migrated = convert_image_configuration(v1_photo_action_config())
        return migrated, ConfigIntent(
            scope="image.output", group_id="image.output",
            changes=dict(migrated.output_changes), source_id="image.configuration",
            # 多出来的字段用来验证宿主只留下真正写入的字段。
            confidence={**{field: "high" for field in migrated.output_changes},
                        "not_a_field": "high"},
            sources={**migrated.output_sources, "not_a_field": ("v1.not_a_field",)},
            target_kind="image_output_config",
        )

    def prepare(self, current):
        migrated, intent = self.intent()
        api = SimpleNamespace(output_configuration=lambda: current)
        host = self.host_type()
        host.controller = SimpleNamespace(
            native_extensions=SimpleNamespace(image_extension_api=lambda: api))
        differences, scope_status, edit_tokens = [], [], {}
        prepared = host._prepare_image_output_intent(intent, differences, scope_status, edit_tokens)
        return migrated, prepared, differences, scope_status, edit_tokens

    def test_prepare_intents_sends_image_output_intents_through_the_prepare_method(self):
        """``image_output_config`` 必须被路由进 ``_prepare_image_output_intent``。

        把它改名成别的分支后，意图会掉到通用配置路径：既拿不到预处理写入的
        ``image_output_config_state`` 令牌，也会被判定成没有可写目标而消失。
        """
        self.save({"network": {"download_proxy": "http://v2-proxy:3128"}})
        current = public_configuration(self.plugin)
        migrated, intent = self.intent()

        host = self.host_type()
        host.controller = SimpleNamespace(
            native_extensions=SimpleNamespace(image_extension_api=lambda: SimpleNamespace(
                output_configuration=lambda: current)))
        # 通用路径会读 session.application.configuration；生图分支用不到它，
        # 因此这里给一个空对象就足以区分两条路径。
        session = SimpleNamespace(application=SimpleNamespace(configuration=SimpleNamespace()))

        intents, differences, scope_status, edit_tokens = host._prepare_intents(
            session, MigrationPlan(v1_root="", intents=(intent,)))

        self.assertEqual(len(intents), 1)
        self.assertIsNot(intents[0], intent)
        self.assertEqual(set(intents[0].changes), set(migrated.output_changes) - {"network"})
        self.assertEqual(set(intents[0].confidence), set(intents[0].changes))
        self.assertEqual(set(intents[0].sources), set(intents[0].changes))
        self.assertEqual([item["field"] for item in differences], ["image.output.network"])
        self.assertEqual([item["kind"] for item in differences], ["preserved_v2"])
        self.assertEqual(scope_status, [{"scope": "image.output", "group_id": "image.output",
                                         "available": True, "reason": ""}])
        key = ("image.output|image.output|image_output_config|"
               + ",".join(sorted(intents[0].changes)))
        self.assertEqual(edit_tokens, {"private_contact_preferences": digest([]),
                                       "image_output_config_state": current["revision"],
                                       key: current["revision"]})

    def test_fresh_v2_output_keeps_the_whole_intent_and_drops_foreign_metadata(self):
        current = public_configuration(self.plugin)

        migrated, prepared, differences, scope_status, edit_tokens = self.prepare(current)

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.changes, migrated.output_changes)
        self.assertEqual(set(prepared.confidence), set(migrated.output_changes))
        self.assertEqual(set(prepared.sources), set(migrated.output_changes))
        self.assertEqual(differences, [])
        self.assertEqual(scope_status, [{"scope": "image.output", "group_id": "image.output",
                                         "available": True, "reason": ""}])
        key = ("image.output|image.output|image_output_config|"
               + ",".join(sorted(migrated.output_changes)))
        self.assertEqual(edit_tokens, {"image_output_config_state": current["revision"],
                                       key: current["revision"]})

    def test_saved_v2_download_proxy_is_retained_on_the_host_path(self):
        self.save({"network": {"download_proxy": "http://v2-proxy:3128"}})
        current = public_configuration(self.plugin)

        migrated, prepared, differences, _, edit_tokens = self.prepare(current)

        self.assertIsNotNone(prepared)
        self.assertEqual(set(prepared.changes), set(migrated.output_changes) - {"network"})
        self.assertIn("endpoints", prepared.changes)
        self.assertEqual(set(prepared.confidence), set(prepared.changes))
        self.assertEqual(set(prepared.sources), set(prepared.changes))
        self.assertEqual([item["field"] for item in differences], ["image.output.network"])
        self.assertEqual([item["kind"] for item in differences], ["preserved_v2"])
        key = ("image.output|image.output|image_output_config|"
               + ",".join(sorted(prepared.changes)))
        self.assertEqual(edit_tokens, {"image_output_config_state": current["revision"],
                                       key: current["revision"]})

    def test_nothing_writable_returns_none_and_only_the_retention_difference(self):
        first = convert_image_configuration(v1_photo_action_config())
        self.save(first.output_changes)
        current = public_configuration(self.plugin)

        _, prepared, differences, _, edit_tokens = self.prepare(current)

        self.assertIsNone(prepared)
        self.assertEqual([item["field"] for item in differences], ["image.output"])
        self.assertEqual([item["kind"] for item in differences], ["preserved_v2"])
        self.assertEqual(edit_tokens, {"image_output_config_state": current["revision"]})


if __name__ == "__main__":
    unittest.main()
