"""Regression coverage for built-in game defaults, engines, rooms, permissions, and memory."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.api.web import PluginRequest, bind_request_context
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.provider.entities import ProviderRequest
from starlette.requests import Request

from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tools import close_core_reply_tools, inject_core_reply_tools, native_tool_name
from astrbot_host.game_access import ensure_game_access
from astrbot_host.native_extension_api import PREFIX as EXT_PREFIX
from astrbot_host.native_extensions.game.activity_methods import GameActivityMethods
from astrbot_host.native_extensions.game.runtime import GameRuntime
from astrbot_host.native_extensions.game.tunnel import QuickTunnel
from companion_v2.capabilities import CapabilityRepository
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import TaskContext, require
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.games.draw_guess import DrawGuessGame, DrawWord, normalize_strokes
from companion_v2.games.native import provider as game_provider
from companion_v2.games.native.access import GROUPS
from companion_v2.games.pikafish import PikafishService
from companion_v2.games.turtle_soup import _FALLBACK_PUZZLES, SoupPuzzle
from companion_v2.product import PRODUCT_VERSION
from tests import test_astrbot_staging_host as staging_support
from tests import test_game_memory_ingestion as _game_memory_support
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_game_native_blackjack import shoe_with_draws
from tests.test_game_native_pig_dice import request as game_request
from tests.test_game_native_turtle_draw import enabled_plugin
from tests.test_game_native_xiangqi import FakeEngine
from tests.test_management_access import SCOPE

ROOT = Path(__file__).resolve().parents[1]
GAME_EXT = ROOT / "astrbot_host" / "native_extensions" / "game"
VALID_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFxoAAAAASUVORK5CYII="
)


def _load_game_json(filename: str) -> dict:
    return json.loads((GAME_EXT / filename).read_text(encoding="utf-8"))


class GameDefaultsAndConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_defaults_and_runtime_fallbacks_stay_consistent(self):
        schema = _load_game_json("settings-schema.json")
        defaults = _load_game_json("settings-defaults.json")
        self.assertEqual(set(defaults), {"server", "native_v2", "xiangqi", "context", "logging"})
        self.assertEqual(set(schema), set(defaults))
        drift = []
        for section, values in defaults.items():
            items = schema.get(section, {}).get("items", {})
            self.assertEqual(set(values), set(items), f"{section} keys differ between defaults and schema")
            for key, value in values.items():
                declared = items.get(key, {}).get("default")
                if declared != value:
                    drift.append(f"{section}.{key}: schema={declared!r} defaults={value!r}")
        self.assertEqual(drift, [])
        self.assertIs(defaults["server"]["enabled"], True)
        for key in (
            "tictactoe_enabled", "gomoku_enabled", "pig_dice_enabled",
            "blackjack_enabled", "xiangqi_enabled", "turtle_soup_enabled", "draw_guess_enabled",
        ):
            self.assertIs(defaults["native_v2"][key], True, key)
        self.assertEqual(defaults["native_v2"]["pig_dice_target_score"], 50)
        runtime_source = (GAME_EXT / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("_cfg_bool('server.enabled', True)", runtime_source)
        self.assertIn("_cfg_bool('native_v2.gomoku_enabled', True)", runtime_source)

    def test_runtime_reads_pig_dice_target_score_with_legacy_fallback(self):
        with TemporaryDirectory() as root:
            ctx = NS(get_config=lambda: {})
            plugin = GameRuntime(ctx, {"pig_dice": {"target_score": 35}}, Path(root), None)
            self.assertEqual(plugin.native_v2_pig_dice_target_score, 35)
            plugin2 = GameRuntime(
                ctx,
                {"native_v2": {"pig_dice_target_score": 80}, "pig_dice": {"target_score": 35}},
                Path(root),
                None,
            )
            self.assertEqual(plugin2.native_v2_pig_dice_target_score, 80)

    async def test_native_pig_dice_uses_configured_target_score(self):
        with TemporaryDirectory() as root:
            plugin = NS(native_v2_pig_dice_enabled=True, native_v2_pig_dice_target_score=22)
            provider = game_provider.NativeGameProvider(plugin, Path(root), version=PRODUCT_VERSION)
            created = await provider.execute(
                game_request(provider, game_provider.PIG_CREATE, {"difficulty": "normal"}, "pig-create"),
                before_dispatch=lambda: True,
            )
            game = created["result"]["game"]
            self.assertEqual(game["state"]["target_score"], 22)
            with patch("companion_v2.games.pig_dice.secrets.randbelow", side_effect=[5, 5, 5, 5]):
                for idx in range(4):
                    await provider.execute(
                        game_request(provider, game_provider.PIG_ROLL, {"game_id": game["game_id"]}, f"roll-{idx}"),
                        before_dispatch=lambda: True,
                    )
            held = await provider.execute(
                game_request(provider, game_provider.PIG_HOLD, {"game_id": game["game_id"]}, "hold-1"),
                before_dispatch=lambda: True,
            )
            finished = held["result"]["game"]
            self.assertEqual((finished["status"], finished["outcome"]), ("finished", "player"))
            self.assertEqual(finished["state"]["target_score"], 22)


class GameBinaryDetectionTests(unittest.TestCase):
    def test_quick_tunnel_detects_managed_download_dir_and_missing_configured_path(self):
        with TemporaryDirectory() as root:
            download_dir = Path(root) / "cloudflared"
            download_dir.mkdir(parents=True)
            binary_name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
            managed_bin = download_dir / binary_name
            managed_bin.write_bytes(b"stub")

            with patch("astrbot_host.native_extensions.game.tunnel.shutil.which", return_value=None):
                tunnel = QuickTunnel(local_url="http://127.0.0.1:8765", download_dir=download_dir)
                self.assertEqual(tunnel.binary_path(), managed_bin.resolve())
                self.assertEqual(tunnel.binary_source(), "managed")
                status = tunnel.status()
                self.assertTrue(status["installed"])
                self.assertEqual(status["source"], "managed")
                self.assertFalse(status["configured_missing"])

                missing = Path(root) / "nonexistent-cloudflared"
                tunnel_missing = QuickTunnel(
                    local_url="http://127.0.0.1:8765",
                    configured_path=str(missing),
                    download_dir=download_dir,
                )
                status_missing = tunnel_missing.status()
                self.assertTrue(status_missing["configured_missing"])
                self.assertTrue(status_missing["installed"])
                self.assertEqual(status_missing["source"], "managed")

                empty_dir = Path(root) / "empty-cloudflared"
                empty_dir.mkdir()
                tunnel_none = QuickTunnel(
                    local_url="http://127.0.0.1:8765",
                    configured_path=str(missing),
                    download_dir=empty_dir,
                )
                status_none = tunnel_none.status()
                self.assertFalse(status_none["installed"])
                self.assertTrue(status_none["configured_missing"])
                self.assertIn("不存在", status_none["error"])

    def test_pikafish_detects_direct_install_root_and_reports_missing_nnue_or_configured_path(self):
        with TemporaryDirectory() as root:
            install_root = Path(root) / "pikafish"
            install_root.mkdir(parents=True)
            exe = install_root / "pikafish.exe"
            exe.write_bytes(b"stub")
            mgr = PikafishService(data_dir=Path(root))
            without_nnue = mgr.status()
            self.assertEqual(Path(without_nnue["path"]).resolve(), exe.resolve())
            self.assertFalse(without_nnue["available"])
            self.assertTrue(without_nnue["managed"])

            nnue = install_root / "pikafish.nnue"
            nnue.write_bytes(b"nnue")
            ready = mgr.status()
            self.assertTrue(ready["available"])
            self.assertEqual(Path(ready["nnue_path"]).resolve(), nnue.resolve())

            missing = Path(root) / "missing-pikafish.exe"
            mgr_missing_fallback = PikafishService(data_dir=Path(root), configured_path=str(missing))
            status_fallback = mgr_missing_fallback.status()
            self.assertTrue(status_fallback["configured_missing"])
            self.assertTrue(status_fallback["available"])
            self.assertTrue(status_fallback["managed"])
            self.assertIn("不存在", status_fallback["error"])

            empty_root = Path(root) / "empty-data"
            empty_root.mkdir()
            mgr_missing = PikafishService(data_dir=empty_root, configured_path=str(missing))
            status_missing = mgr_missing.status()
            self.assertTrue(status_missing["configured_missing"])
            self.assertFalse(status_missing["available"])
            self.assertIn("不存在", status_missing["error"])


class GameManagementAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix="v2-game-mgmt-")
        self.native = staging_support.WebContext()
        types = staging_support.NativeAdapterTypes(
            "4.27.2", staging_support.FakeEvent, staging_support.FakeProvider,
            NS, NS, staging_support.FakeReverseApi,
        )
        self.controller = staging_support.AstrBotStagingController(
            self.native, staging_support.host_config(),
            Path(self.temporary.name), native_types=types, now=lambda: staging_support.NOW,
        )
        self.controller.register_routes()
        self.routes = {(path, methods[0]): handler for path, handler, methods, _ in self.native.registered_web_apis}
        (await self.controller.initialize_profile()).unwrap()

    async def asyncTearDown(self):
        await self.controller.terminate()
        self.temporary.cleanup()

    async def call(self, path=EXT_PREFIX, *, body=None, username="admin"):
        method = "GET" if body is None else "POST"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        req = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""},
                      receive=receive)
        with bind_request_context(PluginRequest(req, username=username)):
            return await self.routes[(path, method)]()

    async def enable_game(self):
        (await self.controller.start()).unwrap()
        registry = self.controller.native_extensions
        package = registry._lease("game").manifest.package
        CapabilityRepository(self.controller.runtime).acquire(
            extension_id="game", package_id=package.package_id, package_version=package.package_version,
            expected_revision=0, observed_at=staging_support.NOW,
        ).unwrap()
        await registry.reconcile_service("game")
        await registry._lease("game").start()
        return registry.service("game")

    async def test_game_management_snapshot_actions_and_errors(self):
        initial = await self.call()
        self.assertEqual(initial["status"], "ok", initial)
        game_ext = initial["data"]["extensions"]["game"]
        self.assertIn("management", game_ext)
        self.assertIn("xiangqi_engine", game_ext["management"])
        self.assertIn("tunnel", game_ext["management"])
        self.assertTrue(game_ext["management"]["xiangqi_engine"]["guidance"])
        self.assertTrue(game_ext["management"]["tunnel"]["guidance"])

        inactive = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "xiangqi_check", "payload": {},
        })
        self.assertEqual(inactive["data"]["code"], "native_service_not_running")

        service = await self.enable_game()
        denied = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "xiangqi_check", "payload": {},
        }, username="api_key:synthetic")
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")

        async def fake_install_xiangqi():
            self.assertFalse(self.controller._control_lock.locked())
            return {"available": True, "managed": True, "version": "2026.1"}

        async def fake_install_cloudflared():
            self.assertFalse(self.controller._control_lock.locked())
            return {"installed": True, "source": "managed"}

        service.xiangqi_engine.install_latest = fake_install_xiangqi
        service.quick_tunnel.install_latest = fake_install_cloudflared
        service.quick_tunnel.start = AsyncMock(return_value="https://demo.trycloudflare.com")
        service.quick_tunnel.stop = AsyncMock()
        service.room_server.start = AsyncMock()

        installed_xq = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "xiangqi_install", "payload": {},
        })
        self.assertEqual(installed_xq["status"], "ok", installed_xq)
        self.assertTrue(installed_xq["data"]["result"]["completed"])

        installed_cf = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "cloudflared_install", "payload": {},
        })
        self.assertEqual(installed_cf["status"], "ok", installed_cf)

        started_tunnel = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "tunnel_start", "payload": {},
        })
        self.assertEqual(started_tunnel["status"], "ok", started_tunnel)
        self.assertEqual(started_tunnel["data"]["result"]["url"], "https://demo.trycloudflare.com")

        stopped_tunnel = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "tunnel_stop", "payload": {},
        })
        self.assertEqual(stopped_tunnel["status"], "ok", stopped_tunnel)

        service.xiangqi_engine.install_latest = AsyncMock(side_effect=PermissionError("disabled"))
        disabled_err = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "xiangqi_install", "payload": {},
        })
        self.assertEqual(disabled_err["data"]["code"], "native_game_download_disabled")

        service.public_base_url = "https://games.example.com"
        fixed_err = await self.call(EXT_PREFIX + "/action", body={
            "extension_id": "game", "action": "tunnel_start", "payload": {},
        })
        self.assertEqual(fixed_err["data"]["code"], "native_game_external_url_configured")


class DrawGuessRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_drawer_guesser_isolation_canvas_strokes_and_bot_guess(self):
        with TemporaryDirectory() as root:
            guesses_requested = []

            async def fake_native_guess(game, image_data_url, *, user_id="", session_id=""):
                guesses_requested.append((game.target.answer, image_data_url, user_id, session_id))
                return "苹果"

            plugin = enabled_plugin(native_guess_drawing=fake_native_guess)
            provider = game_provider.NativeGameProvider(plugin, Path(root), version=PRODUCT_VERSION)
            with patch.object(game_provider.random, "choice", return_value=DrawWord("苹果", ("apple",))):
                opened = await provider.execute(
                    game_request(provider, game_provider.DRAW_CREATE, {"difficulty": "easy"}, "draw-create"),
                    before_dispatch=lambda: True,
                )
            llm_view = opened["result"]["game"]
            self.assertNotIn("苹果", json.dumps(llm_view, ensure_ascii=False))
            self.assertNotIn("answer", llm_view["state"])

            game_id = llm_view["game_id"]
            drawer_ticket = provider.issue_web_ticket(game_id, role="drawer")
            guesser_ticket = provider.issue_web_ticket(game_id, role="guesser")

            drawer_state = (await provider.web_state(drawer_ticket))["game"]
            self.assertEqual(drawer_state["state"]["viewer_role"], "drawer")
            self.assertEqual(drawer_state["state"]["answer"], "苹果")

            guesser_state = (await provider.web_state(guesser_ticket))["game"]
            self.assertEqual(guesser_state["state"]["viewer_role"], "guesser")
            self.assertNotIn("answer", guesser_state["state"])
            self.assertNotIn("苹果", json.dumps(guesser_state, ensure_ascii=False))

            # Role enforcement: guesser cannot draw, drawer cannot submit manual guess
            with self.assertRaisesRegex(game_provider.NativeGameError, "game_drawer_only"):
                await provider.web_action(guesser_ticket, {"action": "clear"})
            with self.assertRaisesRegex(game_provider.NativeGameError, "game_guesser_only"):
                await provider.web_action(drawer_ticket, {"action": "guess", "guess": "苹果"})

            strokes = [{"color": "#d9483b", "width": 6, "points": [[0.1, 0.2], [0.5, 0.5]]}]
            drawn = (await provider.web_action(drawer_ticket, {"action": "strokes", "strokes": strokes}))["game"]
            self.assertEqual(drawn["state"]["revision"], 1)
            self.assertEqual(len(drawn["state"]["strokes"]), 1)

            # Guesser immediately sees drawer's strokes without seeing answer
            guesser_live = (await provider.web_state(guesser_ticket))["game"]
            self.assertEqual(len(guesser_live["state"]["strokes"]), 1)
            self.assertNotIn("answer", guesser_live["state"])

            # Undo and re-draw survive persistence
            undone = (await provider.web_action(drawer_ticket, {"action": "undo"}))["game"]
            self.assertEqual(undone["state"]["strokes"], [])
            await provider.web_action(drawer_ticket, {"action": "strokes", "strokes": strokes})

            # Bot visual guess matches authoritatively and finishes game
            guessed = (await provider.web_action(drawer_ticket, {
                "action": "bot_guess", "image_data_url": VALID_PNG_DATA_URL,
            }))["game"]
            self.assertEqual((guessed["status"], guessed["outcome"]), ("finished", "player"))
            self.assertEqual(guessed["state"]["answer"], "苹果")
            self.assertEqual(len(guesses_requested), 1)
            self.assertEqual(guesses_requested[0][1], VALID_PNG_DATA_URL)

            # After finishing, guesser also sees the revealed answer
            guesser_done = (await provider.web_state(guesser_ticket))["game"]
            self.assertEqual(guesser_done["state"]["answer"], "苹果")

            # Closing an active game via LLM does NOT reveal the answer
            with patch.object(game_provider.random, "choice", return_value=DrawWord("太阳")):
                opened2 = await provider.execute(
                    game_request(provider, game_provider.DRAW_CREATE, {"difficulty": "easy"}, "draw-create-2"),
                    before_dispatch=lambda: True,
                )
            closed2 = await provider.execute(
                game_request(provider, game_provider.DRAW_CLOSE,
                             {"game_id": opened2["result"]["game"]["game_id"]}, "draw-close-2"),
                before_dispatch=lambda: True,
            )
            self.assertEqual(closed2["result"]["game"]["status"], "closed")
            self.assertNotIn("太阳", json.dumps(closed2["result"]["game"], ensure_ascii=False))
            self.assertNotIn("answer", closed2["result"]["game"]["state"])

    async def test_slow_llm_and_vision_awaits_do_not_block_other_rooms_and_preserve_concurrent_strokes(self):
        with TemporaryDirectory() as root:
            turtle_entered = asyncio.Event()
            turtle_release = asyncio.Event()
            vision_entered = asyncio.Event()
            vision_release = asyncio.Event()

            async def slow_turtle(**_kwargs):
                turtle_entered.set()
                await turtle_release.wait()
                return None

            async def slow_vision(_game, _img, **_kwargs):
                vision_entered.set()
                await vision_release.wait()
                return "香蕉"

            plugin = enabled_plugin(
                native_v2_tictactoe_enabled=True,
                generate_native_turtle_soup_puzzle=slow_turtle,
                native_guess_drawing=slow_vision,
            )
            provider = game_provider.NativeGameProvider(plugin, Path(root), version=PRODUCT_VERSION)

            # Create an unrelated TicTacToe room to probe server responsiveness
            ttt = await provider.execute(
                game_request(provider, game_provider.CREATE, {"difficulty": "easy"}, "ttt-1", subject="other-player"),
                before_dispatch=lambda: True,
            )
            ttt_ticket = provider.issue_web_ticket(ttt["result"]["game"]["game_id"])

            # 1. Slow TURTLE_CREATE must not block other room's web_state / web_action
            turtle_task = asyncio.create_task(provider.execute(
                game_request(provider, game_provider.TURTLE_CREATE, {}, "slow-soup"),
                before_dispatch=lambda: True,
            ))
            await asyncio.wait_for(turtle_entered.wait(), 2)
            state_during_turtle = await asyncio.wait_for(provider.web_state(ttt_ticket), 1)
            self.assertEqual(state_during_turtle["game"]["game_type"], "tictactoe")
            moved_during_turtle = await asyncio.wait_for(
                provider.web_action(ttt_ticket, {"action": "move", "row": 1, "column": 1}), 1,
            )
            self.assertEqual(moved_during_turtle["game"]["game_type"], "tictactoe")
            turtle_release.set()
            turtle_res = await asyncio.wait_for(turtle_task, 2)
            self.assertEqual(turtle_res["state"], "succeeded")

            # 2. Slow create_mobile_game("turtle_soup") must not block other rooms
            turtle_entered.clear()
            turtle_release.clear()
            mobile_task = asyncio.create_task(provider.create_mobile_game("mobile-owner-1", "turtle_soup"))
            await asyncio.wait_for(turtle_entered.wait(), 2)
            state_during_mobile = await asyncio.wait_for(provider.web_state(ttt_ticket), 1)
            self.assertEqual(state_during_mobile["game"]["game_type"], "tictactoe")
            turtle_release.set()
            mobile_res = await asyncio.wait_for(mobile_task, 2)
            self.assertEqual(mobile_res["game"]["game_type"], "turtle_soup")

            # 3. Slow WEB_DRAW_BOT_GUESS must not block other rooms AND must preserve concurrent strokes
            with patch.object(game_provider.random, "choice", return_value=DrawWord("苹果")):
                draw = await provider.execute(
                    game_request(provider, game_provider.DRAW_CREATE, {"difficulty": "easy"}, "draw-conc"),
                    before_dispatch=lambda: True,
                )
            draw_ticket = provider.issue_web_ticket(draw["result"]["game"]["game_id"], role="drawer")
            stroke1 = [{"color": "#1c2420", "width": 6, "points": [[0.1, 0.1], [0.2, 0.2]]}]
            await provider.web_action(draw_ticket, {"action": "strokes", "strokes": stroke1})

            bot_guess_task = asyncio.create_task(
                provider.web_action(draw_ticket, {"action": "bot_guess", "image_data_url": VALID_PNG_DATA_URL})
            )
            await asyncio.wait_for(vision_entered.wait(), 2)
            # Other room stays responsive
            self.assertEqual((await asyncio.wait_for(provider.web_state(ttt_ticket), 1))["game"]["game_type"], "tictactoe")
            # Drawer adds a second stroke while vision model is thinking
            stroke2 = stroke1 + [{"color": "#d9483b", "width": 8, "points": [[0.4, 0.4], [0.8, 0.8]]}]
            await asyncio.wait_for(provider.web_action(draw_ticket, {"action": "strokes", "strokes": stroke2}), 1)
            vision_release.set()
            guessed = await asyncio.wait_for(bot_guess_task, 2)
            self.assertEqual(len(guessed["game"]["state"]["guesses"]), 1)
            self.assertEqual(len(guessed["game"]["state"]["strokes"]), 2)

            # 4. Closing the draw_guess room while bot_guess awaits fails cleanly without resurrecting room
            vision_entered.clear()
            vision_release.clear()
            bot_guess_task2 = asyncio.create_task(
                provider.web_action(draw_ticket, {"action": "bot_guess", "image_data_url": VALID_PNG_DATA_URL})
            )
            await asyncio.wait_for(vision_entered.wait(), 2)
            await asyncio.wait_for(provider.web_action(draw_ticket, {"action": "close"}), 1)
            vision_release.set()
            with self.assertRaisesRegex(game_provider.NativeGameError, "game_not_active"):
                await asyncio.wait_for(bot_guess_task2, 2)
            self.assertEqual((await provider.web_state(draw_ticket))["game"]["status"], "closed")

    def test_normalize_strokes_bounds_and_sanitizes(self):
        cleaned = normalize_strokes([
            {"color": "#ABC123", "width": 6, "points": [[0.1234567, 0.9876543], [0.25, 0.75]]},
        ])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["color"], "#abc123")
        self.assertEqual(cleaned[0]["width"], 6.0)
        self.assertEqual(cleaned[0]["points"][0], [0.12346, 0.98765])
        with self.assertRaises(ValueError):
            normalize_strokes([{"color": "#111827", "width": 99, "points": [[0.5, 0.5]]}])
        with self.assertRaises(ValueError):
            normalize_strokes([{"color": "not-a-color", "width": 4, "points": [[0.5, 0.5]]}])
        with self.assertRaises(ValueError):
            normalize_strokes([{"color": "#111827", "width": 4, "points": [[-0.2, 0.5]]}])

    async def test_guess_drawing_preserves_task_vision_provider_without_override(self):
        class Harness(GameActivityMethods):
            def __init__(self, task_provider, fallback_provider):
                self._task_provider = task_provider
                self.draw_guess_vision_provider_id = ""
                self.context = NS(get_using_provider=lambda *_a: fallback_provider)

            def _task_vision_provider(self, _room):
                return self._task_provider

        called = []

        async def task_chat(**kwargs):
            called.append(("task", kwargs))
            return NS(completion_text="苹果")

        async def fallback_chat(**kwargs):
            called.append(("fallback", kwargs))
            return NS(completion_text="香蕉")

        harness = Harness(NS(text_chat=task_chat), NS(text_chat=fallback_chat))
        room = NS(session_id="sess-1", room_id="r-1", game_type="draw_guess")
        game = DrawGuessGame(target=DrawWord("苹果"), max_guesses=3)
        result = await harness._guess_drawing(room, game, VALID_PNG_DATA_URL)
        self.assertEqual(result, "苹果")
        self.assertEqual([item[0] for item in called], ["task"])


class TurtleSoupRepairTests(unittest.IsolatedAsyncioTestCase):
    def test_fallback_bank_is_expanded_and_self_consistent(self):
        self.assertGreaterEqual(len(_FALLBACK_PUZZLES), 10)
        levels = {item["content_level"] for item in _FALLBACK_PUZZLES}
        self.assertEqual(levels, {"all_ages", "normal", "unrestricted"})
        for puzzle in _FALLBACK_PUZZLES:
            self.assertGreaterEqual(len(puzzle["key_facts"]), 3, puzzle["title"])
            self.assertGreaterEqual(len(puzzle["hints"]), 5, puzzle["title"])

    async def test_turtle_soup_prefers_dynamic_ai_puzzle_and_falls_back_smoothly(self):
        with TemporaryDirectory() as root:
            ai_puzzle = SoupPuzzle(
                title="雨夜铃声",
                surface="大雨夜里家里座机响了三声就挂断，他没有回拨，反而立刻安心地熄灯睡着了。这是为什么？",
                solution="他和外出值夜班的家人事先约定好，只要深夜平安抵达值班地点，就给家里座机拨响三声后挂断作为报平安的暗号，不必通话打扰休息，因此他听完三声就安心入睡了。",
                key_facts=("电话来自外出值班的家人", "响三声就挂断是事先约定的报平安暗号", "说明家人已平安到达所以他安心入睡"),
                acceptable_variants=("家人值夜班用响三声挂断的约定暗号报平安",),
                hints=("响三声并不是骚扰电话", "对方故意不接通", "这是事先说好的暗号", "和外出值班的家人有关", "到岗后响三声报平安"),
                content_level="all_ages",
                theme="生活约定",
                trick="三声挂断是报平安暗号",
            )
            calls = []

            async def fake_generate(**kwargs):
                calls.append(kwargs)
                return ai_puzzle if len(calls) == 1 else None

            plugin = enabled_plugin(generate_native_turtle_soup_puzzle=fake_generate)
            provider = game_provider.NativeGameProvider(plugin, Path(root), version=PRODUCT_VERSION)

            first = await provider.execute(
                game_request(provider, game_provider.TURTLE_CREATE, {}, "soup-ai"),
                before_dispatch=lambda: True,
            )
            self.assertEqual(first["result"]["game"]["state"]["puzzle"]["title"], "雨夜铃声")
            ticket = provider.issue_web_ticket(first["result"]["game"]["game_id"])
            hinted = (await provider.web_action(ticket, {"action": "hint"}))["game"]
            self.assertEqual(hinted["state"]["entries"][-1]["kind"], "hint")
            self.assertEqual(hinted["state"]["entries"][-1]["response"], "响三声并不是骚扰电话")

            await provider.execute(
                game_request(provider, game_provider.TURTLE_CLOSE, {"game_id": first["result"]["game"]["game_id"]}, "soup-close"),
                before_dispatch=lambda: True,
            )
            second = await provider.execute(
                game_request(provider, game_provider.TURTLE_CREATE, {}, "soup-fallback"),
                before_dispatch=lambda: True,
            )
            self.assertNotEqual(second["result"]["game"]["state"]["puzzle"]["title"], "雨夜铃声")
            self.assertTrue(second["result"]["game"]["state"]["puzzle"]["surface"])
            self.assertEqual(len(calls), 2)

    async def test_activity_generate_native_turtle_soup_puzzle_wires_ai_validation(self):
        class Harness(GameActivityMethods):
            def __init__(self, replies):
                self._replies = list(replies)
                self.context = NS(get_using_provider=lambda *_a: NS(text_chat=self._chat))

            async def _chat(self, **_kwargs):
                return NS(completion_text=self._replies.pop(0))

        puzzle_json = json.dumps({
            "title": "最后一班车",
            "surface": "雨夜站台上只剩他一人，看到最后一班公交空无一人缓缓进站并打开车门，他却挥挥手没有上车，而是转身开心回家。为什么？",
            "solution": "他是这班末班车的线路调度员，确认末班车准点发车且车厢安全无滞留乘客后，当天值班结束便下班回家了。",
            "key_facts": ["他不是等车的乘客", "他是公交总站的调度员", "确认末班车准点发车后下班回家"],
            "acceptable_variants": ["站务调度员送走末班车后下班"],
            "hints": ["关注他的职业身份", "他本来就不打算乘车", "他在确认车辆准点出发", "和公交站工作有关", "调度员送走末班车后下班"],
            "theme": "职业身份误导",
            "trick": "不是乘客而是调度员",
        }, ensure_ascii=False)
        harness = Harness([puzzle_json, '{"valid": true}'])
        puzzle = await harness.generate_native_turtle_soup_puzzle(difficulty="normal", content_level="all_ages")
        self.assertIsNotNone(puzzle)
        self.assertEqual(puzzle.title, "最后一班车")

        rejected_harness = Harness([puzzle_json, '{"valid": false}', puzzle_json, '{"valid": false}'])
        self.assertIsNone(await rejected_harness.generate_native_turtle_soup_puzzle(difficulty="normal"))


class GameConversationCarryoverAndAccessTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def make_controller(self, config=None):
        if config is None:
            config = multi_config(targets=[
                dict(target_id="first", platform_id="qq-main", account_id="100", bot_id="bot-a", participant_id="200"),
                dict(target_id="second", platform_id="qq-second", account_id="101", bot_id="bot-b", participant_id="300"),
            ])
        return super().make_controller(config)

    async def api(self, *, target="first", body=None):
        inventory = self.controller.web_api.target_inventory()
        selected = next(item for item in inventory if item["target_id"] == target)
        path = selected["roles"][0]["game_access_api"]
        method = "GET" if body is None else "POST"
        if body is not None:
            path += "/configure"

        async def receive():
            return {"type": "http.request", "body": json.dumps(body).encode("utf-8"), "more_body": False}

        req = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""},
                      receive=receive)
        handler = next(handler for route, handler, methods, _ in self.native.registered_web_apis
                       if route == path and method in methods)
        with bind_request_context(PluginRequest(req, username="admin")):
            return await handler()

    async def test_game_access_configure_reads_status_after_reconcile(self):
        self.controller.register_routes()
        await self.start_host()
        initial = (await self.api())["data"]
        order = []
        original_reconcile = self.controller.native_extensions.reconcile_service

        async def tracked_reconcile(name):
            order.append(("reconcile", name))
            return await original_reconcile(name)

        from companion_v2.games.native.access import NativeGameAccess
        original_status = NativeGameAccess.status

        def tracked_status(access_self, operator, at):
            order.append(("status",))
            return original_status(access_self, operator, at)

        with patch.object(self.controller.native_extensions, "reconcile_service", side_effect=tracked_reconcile), \
                patch.object(NativeGameAccess, "status", autospec=True, side_effect=tracked_status):
            res = await self.api(body={
                "game_type": "gomoku", "enabled": True,
                "edit_token": initial["edit_token"], "operation_key": "order-check",
            })
        self.assertEqual(res["status"], "ok", res)
        self.assertIn("runtime", res["data"])
        self.assertEqual(order[-2:], [("reconcile", "game"), ("status",)])

    async def test_new_conversation_carries_game_access_for_regular_and_core_users_while_honoring_revoke(self):
        self.controller.register_routes()
        app = await self.start_host()
        # Grant gomoku for second (non-core user) in initial conversation, keep tictactoe revoked/ungranted
        second_initial = (await self.api(target="second"))["data"]
        granted = await self.api(target="second", body={
            "game_type": "gomoku", "enabled": True,
            "edit_token": second_initial["edit_token"], "operation_key": "grant-second-gomoku",
        })
        self.assertEqual(granted["status"], "ok", granted)
        # Explicitly grant then revoke tictactoe on second so a REVOKED route record exists
        granted_ttt = await self.api(target="second", body={
            "game_type": "tictactoe", "enabled": True,
            "edit_token": granted["data"]["status"]["edit_token"], "operation_key": "grant-second-ttt",
        })
        revoked_ttt = await self.api(target="second", body={
            "game_type": "tictactoe", "enabled": False,
            "edit_token": granted_ttt["data"]["status"]["edit_token"], "operation_key": "revoke-second-ttt",
        })
        self.assertEqual(revoked_ttt["status"], "ok", revoked_ttt)

        # Rotate conversation_id (/new) for second (non-core user) on the same private route
        self.native.conversations["qq-second"] = "conversation-b-after-new"
        new_event_second = self.event(target="second", text="再来一局五子棋", identifier="after-new-second")
        prepared_second = (await self.controller.prepare_core_reply(new_event_second)).unwrap()
        await prepared_second["native_source_future"]
        rotated_ctx = prepared_second["context"]
        self.assertIn("conversation-b-after-new", rotated_ctx.scope.conversation_id)
        prepare_core_reply_event(
            new_event_second,
            prepared_second,
            authorize=lambda value: self.controller.authorize_core_reply(new_event_second, value),
        )
        req_second = ProviderRequest(system_prompt="persona")
        inject_core_reply_tools(new_event_second, req_second, application=app)
        offered_second = {item.action for item in app.tools.external.catalog_for(rotated_ctx)}
        self.assertTrue(GROUPS["gomoku"].issubset(offered_second))
        self.assertTrue(GROUPS["tictactoe"].isdisjoint(offered_second))
        self.assertTrue(GROUPS["xiangqi"].isdisjoint(offered_second))

        # Core admin (first): promote to CORE tier, revoke tictactoe, rotate conversation_id (/new)
        first_target = self.controller.settings.target("first")
        first_subject = self.controller.authority.subject(first_target)
        policy = ObjectAccessPolicy(app.runtime)
        policy.set_tier(
            first_subject,
            AuthorizationTier.CORE,
            expected_revision=policy.revision,
            observed_at=self.clock.now(),
        )
        first_initial = (await self.api(target="first"))["data"]
        first_revoked = await self.api(target="first", body={
            "game_type": "tictactoe", "enabled": False,
            "edit_token": first_initial["edit_token"], "operation_key": "revoke-first-ttt",
        })
        self.assertEqual(first_revoked["status"], "ok", first_revoked)
        self.native.conversations["qq-main"] = "conversation-a-after-new"
        first_event = self.event(target="first", text="新会话来玩五子棋", identifier="after-new-first")
        prepared_first = (await self.controller.prepare_core_reply(first_event)).unwrap()
        await prepared_first["native_source_future"]
        rotated_core = prepared_first["context"]
        self.assertIn("conversation-a-after-new", rotated_core.scope.conversation_id)
        prepare_core_reply_event(
            first_event,
            prepared_first,
            authorize=lambda value: self.controller.authorize_core_reply(first_event, value),
        )
        req_first = ProviderRequest(system_prompt="persona")
        inject_core_reply_tools(first_event, req_first, application=app)
        offered_core = {item.action for item in app.tools.external.catalog_for(rotated_core)}
        self.assertTrue(GROUPS["gomoku"].issubset(offered_core))
        self.assertTrue(GROUPS["tictactoe"].isdisjoint(offered_core))

        # projection() failure must fail-closed even for core admin
        from companion_v2.foundation.outcome import Outcome
        with patch.object(
            app.tools.external.capabilities,
            "projection",
            return_value=Outcome.failed("capability_descriptor_missing", "missing"),
        ):
            offered_broken = {item.action for item in app.tools.external.catalog_for(rotated_core)}
        self.assertFalse(any(action.startswith("game.") for action in offered_broken))

        # Pausing the game module must block even core admin
        cap_repo = CapabilityRepository(app.runtime)
        installed = cap_repo.installation(extension_id="game").unwrap()
        cap_repo.pause(extension_id="game", expected_revision=installed.revision, observed_at=self.clock.now()).unwrap()
        offered_paused = {item.action for item in app.tools.external.catalog_for(rotated_core)}
        self.assertFalse(any(action.startswith("game.") for action in offered_paused))


class GameMemoryRepairTests(_game_memory_support.GameMemoryTests):
    async def test_natural_blackjack_with_room_access_and_web_terminal_write_governed_experiences(self):
        host = await self.game_host()
        app = host.application
        context = TaskContext(
            self.important,
            require(self.identity.resolve_role(SCOPE, observed_at=self.clock.now())),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        # 1. Attach web terminal callback (mirroring NativeExtensionRegistry)
        self.game_port.attach_web_terminal_callback(
            lambda ctx, finished_game: app.memory.game.record_web_terminal(ctx, finished_game)
        )

        # 2. Natural 21 result carrying room_access + room_url must NOT be skipped by game_ingestion
        original_execute = self.game_port.execute

        async def execute_with_room_access(req, *, before_dispatch):
            res = await original_execute(req, before_dispatch=before_dispatch)
            if isinstance(res.get("result"), dict) and "game" in res["result"]:
                res["result"]["room_access"] = {"url": "http://127.0.0.1:8765/native/tok"}
                res["result"]["room_url"] = "http://127.0.0.1:8765/native/tok"
            return res

        with patch.object(self.game_port, "execute", side_effect=execute_with_room_access), \
                patch.object(game_provider, "fresh_shoe", return_value=shoe_with_draws(["A", "K", "9", "8"])):
            _, row, _, game = await self.operate(host, game_provider.BLACKJACK_CREATE,
                                                 {"difficulty": "normal"}, "natural-with-room")
        self.assertEqual((game["status"], game["outcome"]), ("finished", "player"))
        recorded = app.memory.game.record(context, row["invocation_id"]).unwrap()
        self.assertTrue(recorded["view_id"])
        self.assertIn("二十一点终局：玩家获胜", app.memory.experiences.list(context).unwrap()["items"][0]["title"])

        # 3. Chat-created game finished on the web room writes governed, revocable, idempotent experience
        with patch.object(game_provider, "fresh_shoe", return_value=shoe_with_draws(["10", "6", "9", "8", "5"])):
            _, _, _, active_game = await self.operate(host, game_provider.BLACKJACK_CREATE,
                                                      {"difficulty": "normal"}, "web-create")
        self.assertEqual(active_game["status"], "active")
        scope_key = next(iter(self.game_port.store.data["scopes"]))
        self.game_port.bind_game_context(active_game["game_id"], context, scope=scope_key)
        ticket = self.game_port.issue_web_ticket(active_game["game_id"])
        stood = await self.game_port.web_action(ticket, {"action": "stand"})
        self.assertEqual(stood["game"]["status"], "finished")

        experiences = app.memory.experiences.list(context).unwrap()["items"]
        self.assertEqual(len(experiences), 2)
        web_exp = next(item for item in experiences if active_game["game_id"] in item["statements"][0]["text"])
        self.assertIn("二十一点终局", web_exp["title"])
        # Idempotent re-call does not duplicate experience
        again = app.memory.game.record_web_terminal(context, stood["game"]).unwrap()
        self.assertEqual(again["view_id"], web_exp["view_id"])
        self.assertEqual(len(app.memory.experiences.list(context).unwrap()["items"]), 2)
        # Identity boundary enforced
        self.assertFalse(app.memory.experiences.read(replace(context, actor=self.core), web_exp["view_id"]).is_success)


if __name__ == "__main__":
    unittest.main()
