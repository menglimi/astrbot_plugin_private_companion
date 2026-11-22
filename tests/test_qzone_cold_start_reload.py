"""Cold-start QQ Zone readiness drift and unlatched-bridge detection contracts."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.qzone_transport import COOKIE_FILE_NAME, build_qzone_transport
from companion_v2.qzone.capability import CAPABILITIES as QZONE_CAPABILITIES
from tests.test_host_application import HostFixture


class _OptionalStubModule(ModuleType):
    def __init__(self, name):
        super().__init__(name)
        self.__path__ = []

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        value = type(item, (), {})
        setattr(self, item, value)
        return value


class _OptionalHostFinder:
    PREFIXES = ("astrbot", "aiocqhttp", "sqlmodel", "defusedxml")

    def find_spec(self, fullname, path, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in self.PREFIXES):
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return _OptionalStubModule(spec.name)

    def exec_module(self, module):
        pass


_astrbot_import_root = None


def _import_multi_controller():
    global _astrbot_import_root
    if importlib.util.find_spec("astrbot") is not None:
        if os.environ.get("ASTRBOT_ROOT") is None:
            _astrbot_import_root = TemporaryDirectory(prefix="companion-v2-qzone-reload-import-")
            os.environ["ASTRBOT_ROOT"] = _astrbot_import_root.name
        import astrbot_host.multi_controller as module
        return module
    finder = _OptionalHostFinder()
    sys.meta_path.append(finder)
    try:
        import astrbot_host.multi_controller as module
        return module
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


MULTI_CONTROLLER = _import_multi_controller()
AstrBotMultiController = MULTI_CONTROLLER.AstrBotMultiController


def tearDownModule():
    global _astrbot_import_root
    if _astrbot_import_root is not None:
        os.environ.pop("ASTRBOT_ROOT", None)
        _astrbot_import_root.cleanup()
        _astrbot_import_root = None


def _fake_aiocqhttp_platform(platform_id="qq-main", *, online_accounts=()):
    clients = {str(account): object() for account in online_accounts}
    client = NS(_wsr_api_clients=clients)
    meta = NS(name="aiocqhttp", id=platform_id)
    return NS(meta=lambda: meta, get_client=lambda: client, client=client)


def _fake_context(platforms, *, expose_platform_manager=True):
    context = NS(get_platform_inst=lambda platform_id: platforms.get(platform_id))
    if expose_platform_manager:
        class _Manager:
            @property
            def platform_insts(self):
                return tuple(platforms.values())

        context.platform_manager = _Manager()
    return context


def _controller_stub(context, *, data_root, session, accounts=()):
    controller = AstrBotMultiController.__new__(AstrBotMultiController)
    controller.context = context
    controller.data_root = data_root
    controller.session = session
    controller.settings = NS(accounts=tuple(accounts))
    return controller


def _external_tool_names(application):
    external = getattr(application, "external", None)
    return set(getattr(external, "providers", ())) if external is not None else set()


class QzoneColdStartReloadTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def _build_session(self, context, data_root, *, pairs=None, state="running"):
        transport = build_qzone_transport(context, data_root=data_root)
        host = self.host(qzone_transport=transport)
        host.application.state = state
        return NS(
            host=host,
            application=host.application,
            native_bridge_pairs=({}, {}) if pairs is None else pairs,
        )

    def test_cold_start_before_aiocqhttp_triggers_one_time_rebuild_and_mounts_qzone_and_tools(self):
        platforms = {}
        context = _fake_context(platforms)
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            initial = self._build_session(context, root)
            self.assertIsNone(initial.application.qzone)
            self.assertTrue(set(QZONE_CAPABILITIES).isdisjoint(_external_tool_names(initial.application)))

            controller = _controller_stub(
                context,
                data_root=root,
                session=initial,
                accounts=(NS(platform_id="qq-main", self_id="100"),),
            )
            self.assertTrue(controller.native_bridges_current())

            # aiocqhttp loads before the OneBot reverse WebSocket login arrives:
            # get_client() is already non-None, so Qzone transport is now ready.
            platforms["qq-main"] = _fake_aiocqhttp_platform("qq-main", online_accounts=())
            self.assertFalse(controller.native_bridges_current())

            # One rebuild mounts both application.qzone and QzoneToolPort, and the
            # subsequent check stays True even while WebSocket login is still waiting.
            rebuilt = self._build_session(context, root)
            self.assertIsNotNone(rebuilt.application.qzone)
            self.assertTrue(set(QZONE_CAPABILITIES).issubset(_external_tool_names(rebuilt.application)))
            controller.session = rebuilt
            self.assertTrue(controller.native_bridges_current())

    def test_qzone_drift_still_detected_after_recover_accounts_latches_bridges(self):
        platforms = {}
        context = _fake_context(platforms)
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            initial = self._build_session(context, root)
            self.assertIsNone(initial.application.qzone)

            platform = _fake_aiocqhttp_platform("qq-main", online_accounts=("100",))
            platforms["qq-main"] = platform
            # on_companion_platform_loaded calls recover_accounts() first, which can
            # latch native_bridge_pairs before _schedule_host_start checks currentness.
            initial.native_bridge_pairs[0]["qq-main"] = (platform, platform.get_client())
            initial.native_bridge_pairs[1]["qq-main"] = (platform, platform.get_client())

            controller = _controller_stub(
                context,
                data_root=root,
                session=initial,
                accounts=(NS(platform_id="qq-main", self_id="100"),),
            )
            self.assertFalse(controller.native_bridges_current())

            rebuilt = self._build_session(
                context,
                root,
                pairs=(
                    {"qq-main": (platform, platform.get_client())},
                    {"qq-main": (platform, platform.get_client())},
                ),
            )
            self.assertIsNotNone(rebuilt.application.qzone)
            controller.session = rebuilt
            self.assertTrue(controller.native_bridges_current())

    def test_manual_cookie_added_after_cold_start_triggers_rebuild(self):
        context = _fake_context({})
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            initial = self._build_session(context, root)
            controller = _controller_stub(context, data_root=root, session=initial)
            self.assertTrue(controller.native_bridges_current())

            (root / COOKIE_FILE_NAME).write_text("uin=o100; p_skey=synthetic", encoding="utf-8")
            self.assertFalse(controller.native_bridges_current())

            controller.session = self._build_session(context, root)
            self.assertIsNotNone(controller.session.application.qzone)
            self.assertTrue(controller.native_bridges_current())

    def test_unlatched_online_platform_and_waiting_platform_boundaries(self):
        platforms = {}
        # Omit platform_manager so Qzone readiness does not mask bridge-latch behavior.
        context = _fake_context(platforms, expose_platform_manager=False)
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            session = NS(application=NS(state="running", qzone=None), native_bridge_pairs=({}, {}))
            controller = _controller_stub(
                context,
                data_root=root,
                session=session,
                accounts=(
                    NS(platform_id="qq-main", self_id="100"),
                    NS(platform_id="qq-second", self_id="101"),
                ),
            )

            # Adapter loaded without an authenticated WebSocket login stays waiting.
            platforms["qq-main"] = _fake_aiocqhttp_platform("qq-main", online_accounts=())
            self.assertTrue(controller.native_bridges_current())

            # Once a configured account is actually online while snapshots are empty,
            # the unlatched online platform must be reported.
            online_main = _fake_aiocqhttp_platform("qq-main", online_accounts=("100",))
            platforms["qq-main"] = online_main
            self.assertFalse(controller.native_bridges_current())

            # Once one platform is latched, a second configured account coming online
            # keeps partial-online replay semantics intact.
            session.native_bridge_pairs[0]["qq-main"] = (online_main, online_main.get_client())
            session.native_bridge_pairs[1]["qq-main"] = (online_main, online_main.get_client())
            platforms["qq-second"] = _fake_aiocqhttp_platform("qq-second", online_accounts=("101",))
            self.assertTrue(controller.native_bridges_current())

    def test_minimal_test_double_without_qzone_attribute_does_not_false_trigger(self):
        platform = _fake_aiocqhttp_platform("qq-main", online_accounts=("100",))
        context = _fake_context({"qq-main": platform})
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            session = NS(
                application=NS(state="running"),
                native_bridge_pairs=(
                    {"qq-main": (platform, platform.get_client())},
                    {"qq-main": (platform, platform.get_client())},
                ),
            )
            controller = _controller_stub(context, data_root=root, session=session)
            self.assertTrue(controller.native_bridges_current())

    def test_no_aiocqhttp_or_cookie_keeps_build_qzone_transport_none_and_bridges_current(self):
        context = _fake_context({})
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            self.assertIsNone(build_qzone_transport(context, data_root=root))
            session = NS(application=NS(state="running", qzone=None), native_bridge_pairs=({}, {}))
            controller = _controller_stub(context, data_root=root, session=session)
            self.assertTrue(controller.native_bridges_current())

    def test_qzone_readiness_probe_failure_fails_safe_without_looping(self):
        context = _fake_context({})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = NS(application=NS(state="running", qzone=None), native_bridge_pairs=({}, {}))
            controller = _controller_stub(context, data_root=root, session=session)
            with patch.object(MULTI_CONTROLLER, "build_qzone_transport", side_effect=RuntimeError("boom")):
                self.assertTrue(controller.native_bridges_current())

    def test_new_session_missing_ready_qzone_helper_flags_reassemble_once(self):
        platforms = {}
        context = _fake_context(platforms)
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QZONE_COOKIE": ""}):
            root = Path(directory)
            new_session = self._build_session(context, root, state="new")
            controller = _controller_stub(context, data_root=root, session=new_session)
            self.assertFalse(controller._session_missing_ready_qzone(new_session))

            platforms["qq-main"] = _fake_aiocqhttp_platform("qq-main", online_accounts=())
            self.assertTrue(controller._session_missing_ready_qzone(new_session))

            rebuilt = self._build_session(context, root, state="new")
            self.assertFalse(controller._session_missing_ready_qzone(rebuilt))


if __name__ == "__main__":
    unittest.main()
