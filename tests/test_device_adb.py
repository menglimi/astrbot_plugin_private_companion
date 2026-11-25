"""The device layer is transport-only; these tests prove its bounds and parsing."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v2.device.adb import (
    ADB_KEYBOARD_IME, AdbClient, AdbError, ImeTextInput, UiElement, discover_adb, parse_devices,
    parse_ui_hierarchy, select_elements, take_ime_restore_error,
)
from companion_v2.device.session import PhoneAction, PhoneSession, parse_action

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
HIERARCHY = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.app" content-desc="" clickable="false" enabled="true"
        bounds="[0,0][1080,2400]">
    <node index="1" text="签到" resource-id="com.example.app:id/checkin"
          class="android.widget.Button" package="com.example.app" content-desc=""
          clickable="true" enabled="true" bounds="[400,1100][680,1220]" />
    <node index="2" text="今日已签到" resource-id="" class="android.widget.TextView"
          package="com.example.app" content-desc="" clickable="false" enabled="true"
          bounds="[300,1500][780,1580]" />
    <node index="3" text="" resource-id="" class="android.widget.ImageView"
          package="com.example.app" content-desc="返回" clickable="true" enabled="true"
          bounds="[20,80][120,180]" />
  </node>
</hierarchy>
"""


class FakeAdb:
    """A deterministic stand-in: each call is matched by its tail and recorded."""

    def __init__(self):
        self.calls = []
        self.responses = {
            ("devices", "-l"): "List of devices attached\nSER123\tdevice product:panther model:Pixel_7 transport_id:3\n",
            ("-s", "SER123", "shell", "wm", "size"): "Physical size: 1080x2400\n",
            ("-s", "SER123", "exec-out", "uiautomator", "dump", "--compressed", "/dev/tty"): HIERARCHY,
            ("-s", "SER123", "exec-out", "uiautomator", "dump", "/dev/tty"): HIERARCHY,
            ("-s", "SER123", "shell", "dumpsys", "power"):
                "mWakefulness=Awake\nDisplay Power: state=ON\n",
            ("-s", "SER123", "shell", "dumpsys", "window", "policy"):
                "KeyguardServiceDelegate\n  showing=false\n  secure=true\n",
            ("-s", "SER123", "shell", "input", "keyevent", "WAKEUP"): "",
            ("-s", "SER123", "shell", "am", "force-stop", "com.example.app"): "",
            ("-s", "SER123", "exec-out", "screencap", "-p"): PNG,
            ("-s", "SER123", "shell", "input", "tap", "540", "1160"): "",
            ("-s", "SER123", "shell", "input", "tap", "10", "10"): "",
            ("-s", "SER123", "shell", "monkey", "-p", "com.example.app", "-c",
             "android.intent.category.LAUNCHER", "1"): "Events injected: 1\n",
            ("-s", "SER123", "shell", "input", "text", "hello%sworld"): "",
            ("-s", "SER123", "shell", "input", "keyevent", "BACK"): "",
            ("-s", "SER123", "shell", "ime", "list", "-s"):
                "com.android.adbkeyboard/.AdbIME\n"
                "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME\n",
            ("-s", "SER123", "shell", "settings", "get", "secure", "default_input_method"):
                "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME\n",
            ("-s", "SER123", "shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"): "",
            ("-s", "SER123", "shell", "ime", "set",
             "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"): "",
            ("-s", "SER123", "shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg",
             "5L2g5aW9"): "Broadcasting: Intent { act=ADB_INPUT_B64 }\n",
            ("-s", "SER123", "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"):
                "Broadcasting: Intent { act=ADB_CLEAR_TEXT }\n",
        }
        self.failures = {}

    def __call__(self, args, timeout):
        full = tuple(args)
        self.calls.append(full)
        # The client always passes its executable as argv[0], like subprocess.
        args = full[1:] if full and str(full[0]).endswith(("adb.exe", "adb")) else full
        if args in self.failures:
            return subprocess.CompletedProcess(full, 1, b"", self.failures[args].encode())
        if "broadcast" in args and "ADB_INPUT_B64" in args:
            return subprocess.CompletedProcess(full, 0, b"Broadcasting: Intent { act=ADB_INPUT_B64 }\n", b"")
        if args not in self.responses:
            return subprocess.CompletedProcess(full, 1, b"", b"unexpected command")
        return subprocess.CompletedProcess(full, 0, self.responses[args], b"")


class ChangingAdb(FakeAdb):
    """Every mutating command advances a visible frame, like a real UI would."""

    def __init__(self):
        super().__init__()
        self.version = 0

    def __call__(self, args, timeout):
        full = tuple(args)
        tail = full[1:] if full and str(full[0]).endswith(("adb.exe", "adb")) else full
        if "screencap" in tail:
            self.calls.append(full)
            return subprocess.CompletedProcess(full, 0, PNG + bytes([self.version]), b"")
        if "uiautomator" in tail and "dump" in tail:
            self.calls.append(full)
            return subprocess.CompletedProcess(
                full, 0, HIERARCHY.replace('text="签到"', f'text="签到 v{self.version}"').encode(), b"")
        result = super().__call__(args, timeout)
        if result.returncode == 0 and any(word in tail for word in ("tap", "monkey", "keyevent")):
            self.version += 1
        return result


class AdbParsingTests(unittest.TestCase):
    def test_devices_ignore_daemon_banner_and_header(self):
        devices = parse_devices("* daemon not running\nList of devices attached\nSER123 device model:Pixel_7\n"
                                "SER999 offline\n")
        self.assertEqual([item.serial for item in devices], ["SER123", "SER999"])
        self.assertEqual(devices[0].model, "Pixel_7")
        self.assertEqual(devices[1].state, "offline")

    def test_hierarchy_bounds_and_labels(self):
        elements = parse_ui_hierarchy(HIERARCHY)
        self.assertEqual(len(elements), 4)
        checkin = elements[1]
        self.assertEqual(checkin.label, "签到")
        self.assertTrue(checkin.clickable)
        self.assertEqual(checkin.center, (540, 1160))
        self.assertEqual(elements[3].label, "返回")

    def test_malformed_hierarchy_is_refused(self):
        with self.assertRaises(AdbError):
            parse_ui_hierarchy("not xml at all")

    def test_element_ranking_prefers_clickable_exact_label(self):
        elements = parse_ui_hierarchy(HIERARCHY)
        matches = select_elements(elements, "签到")
        self.assertEqual(matches[0].label, "签到")
        self.assertTrue(matches[0].clickable)

    def test_discover_requires_a_real_executable(self):
        with self.assertRaises(AdbError) as caught:
            discover_adb("C:/definitely/missing/adb.exe")
        self.assertEqual(caught.exception.code, "adb_not_found")

    def test_discover_returns_explicit_path(self):
        path = Path(__file__).resolve()
        self.assertEqual(discover_adb(path), path)


class AdbClientTests(unittest.TestCase):
    def test_pairing_passes_code_through_stdin(self):
        def runner(args, timeout, *, stdin):
            self.assertEqual(args, ["adb.exe", "pair", "192.168.1.20:35125"])
            self.assertEqual(stdin, b"012345\n")
            return subprocess.CompletedProcess(args, 0, b"Successfully paired", b"")

        client = AdbClient("adb.exe", runner=runner)
        self.assertEqual(client.pair("192.168.1.20:35125", "012345"), "Successfully paired")

    def client(self, fake=None, serial="SER123"):
        return AdbClient("adb.exe", serial=serial, runner=fake or FakeAdb())

    def test_single_ready_device_is_selected_and_listed(self):
        client = self.client(serial=None)
        devices = client.devices()
        self.assertEqual([item.serial for item in devices], ["SER123"])
        self.assertEqual(client.shell("wm", "size"), "Physical size: 1080x2400\n")
        self.assertEqual(client.serial, "SER123")

    def test_multiple_devices_without_serial_are_refused(self):
        class TwoDevices(FakeAdb):
            def __call__(self, args, timeout):
                if "devices" in args:
                    return subprocess.CompletedProcess(
                        args, 0, b"List of devices attached\nA device\nB device\n", b"")
                return super().__call__(args, timeout)

        with self.assertRaises(AdbError) as caught:
            self.client(TwoDevices(), serial=None).tap(10, 10)
        self.assertEqual(caught.exception.code, "adb_multiple_devices")

    def test_tap_is_bounded_by_reported_screen(self):
        with self.assertRaises(AdbError) as caught:
            self.client().tap(2000, 10)
        self.assertEqual(caught.exception.code, "adb_tap_out_of_bounds")

    def test_tap_text_clicks_the_element_center(self):
        fake = FakeAdb()
        client = self.client(fake)
        element = client.tap_text("签到")
        self.assertEqual(element.center, (540, 1160))
        self.assertIn(("-s", "SER123", "shell", "input", "tap", "540", "1160"),
                      [call[1:] for call in fake.calls])

    def test_missing_text_is_not_guessed(self):
        with self.assertRaises(AdbError) as caught:
            self.client().tap_text("不存在的按钮")
        self.assertEqual(caught.exception.code, "adb_text_not_found")

    def test_type_escapes_spaces_and_rejects_unicode(self):
        fake = FakeAdb()
        self.client(fake).text("hello world")
        self.assertIn(("-s", "SER123", "shell", "input", "text", "hello%sworld"),
                      [call[1:] for call in fake.calls])
        with self.assertRaises(AdbError) as caught:
            self.client().text("你好")
        self.assertEqual(caught.exception.code, "adb_text_unsupported")

    def test_key_and_package_are_allowlisted(self):
        with self.assertRaises(AdbError):
            self.client().key("HACK")
        with self.assertRaises(AdbError):
            self.client().key("POWER")
        with self.assertRaises(AdbError):
            self.client().launch("not a package")
        self.assertEqual(self.client().launch("com.example.app"), "Events injected: 1")

    def test_display_power_wins_over_wakefulness(self):
        class Aod(FakeAdb):
            def __call__(self, args, timeout):
                if "dumpsys" in args and "power" in args:
                    return subprocess.CompletedProcess(
                        args, 0, b"mWakefulness=Asleep\nDisplay Power: state=ON\n", b"")
                return super().__call__(args, timeout)

        state = self.client(Aod()).display_state()
        self.assertEqual(state["wakefulness"], "asleep")
        self.assertTrue(state["on"])

    def test_read_only_observation_reports_lock_without_sending_device_input(self):
        adb = FakeAdb()
        adb.responses[("-s", "SER123", "shell", "dumpsys", "window", "policy")] = (
            "KeyguardServiceDelegate\n  showing=true\n  secure=false\n")
        session = PhoneSession(self.client(adb), goal="查看手机屏幕")
        with self.assertRaises(AdbError) as caught:
            session.observe(0, read_only=True)
        self.assertEqual(caught.exception.code, "adb_unlock_required")
        self.assertFalse(any("input" in command or "screencap" in command for command in adb.calls))

    def test_ensure_awake_uses_wakeup_and_reports_a_dead_screen(self):
        class Dark(FakeAdb):
            def __call__(self, args, timeout):
                if "dumpsys" in args and "power" in args:
                    return subprocess.CompletedProcess(
                        args, 0, b"mWakefulness=Asleep\nDisplay Power: state=OFF\n", b"")
                return super().__call__(args, timeout)

        fake = Dark()
        with self.assertRaises(AdbError) as caught:
            self.client(fake).ensure_awake()
        self.assertEqual(caught.exception.code, "adb_screen_off")
        calls = [call[1:] for call in fake.calls]
        self.assertIn(("-s", "SER123", "shell", "input", "keyevent", "WAKEUP"), calls)
        self.assertFalse(any("POWER" in call for call in calls))

    def test_force_stop_is_bounded(self):
        with self.assertRaises(AdbError):
            self.client().force_stop("not a package")
        self.assertEqual(self.client().force_stop("com.example.app"), "已强制停止 com.example.app")

    def test_ui_dump_falls_back_when_compressed_is_refused(self):
        class PlainOnly(FakeAdb):
            def __call__(self, args, timeout):
                if "--compressed" in args:
                    return subprocess.CompletedProcess(args, 1, b"", b"unknown option")
                return super().__call__(args, timeout)

        elements = self.client(PlainOnly()).ui_dump()
        self.assertEqual(len(elements), 4)

    def test_screenshot_requires_a_png(self):
        class Broken(FakeAdb):
            def __call__(self, args, timeout):
                if "screencap" in args:
                    return subprocess.CompletedProcess(args, 0, b"not-a-png", b"")
                return super().__call__(args, timeout)

        with self.assertRaises(AdbError):
            self.client(Broken()).screenshot()

    def test_failed_command_keeps_a_stable_code(self):
        fake = FakeAdb()
        fake.failures[("-s", "SER123", "shell", "input", "keyevent", "BACK")] = "boom"
        with self.assertRaises(AdbError) as caught:
            self.client(fake).key("BACK")
        self.assertEqual(caught.exception.code, "adb_failed")
        self.assertIn("boom", str(caught.exception))


class PhoneActionTests(unittest.TestCase):
    def test_parse_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            parse_action({"kind": "tap", "x": 1, "y": 2, "evil": True})

    def test_parse_requires_coordinates_for_tap(self):
        with self.assertRaises(ValueError):
            parse_action({"kind": "tap"})
        self.assertEqual(parse_action({"kind": "tap", "x": 1, "y": 2}).document(), {"kind": "tap", "x": 1, "y": 2})

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            parse_action({"kind": "shell"})


class PhoneSessionTests(unittest.TestCase):
    def test_observe_decide_act_then_done(self):
        fake = ChangingAdb()
        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="打开应用并签到", max_steps=3)

        def decide(observation, history):
            if not history:
                self.assertIn("签到", observation.prompt_text())
                return PhoneAction("launch", package="com.example.app")
            if len(history) == 1:
                return PhoneAction("tap_text", text="签到")
            return PhoneAction("done", reason="已签到")

        outcome = session.run(decide)
        self.assertEqual(outcome.state, "done")
        self.assertEqual([step.action.kind for step in outcome.steps], ["launch", "tap_text", "done"])
        self.assertTrue(all(step.ok for step in outcome.steps))
        self.assertIn("已签到", outcome.summary)

    def test_two_failures_stop_the_session(self):
        fake = FakeAdb()
        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="点击不存在的按钮", max_steps=4)

        def decide(observation, history):
            return PhoneAction("tap_text", text="不存在的按钮")

        outcome = session.run(decide)
        self.assertEqual(outcome.state, "failed")
        self.assertEqual(len(outcome.steps), 2)
        self.assertEqual(outcome.steps[-1].code, "adb_text_not_found")

    def test_a_tap_that_changes_nothing_is_reported_as_ineffective(self):
        fake = FakeAdb()
        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="点击没有反应的区域", max_steps=3,
                               allowed=("tap", "done", "fail"))
        outcome = session.run(lambda observation, history: PhoneAction("tap", x=10, y=10))
        self.assertEqual(outcome.state, "failed")
        self.assertTrue(all(step.code == "phone_action_ineffective" for step in outcome.steps))

    def test_forbidden_action_is_refused_before_touching_the_device(self):
        fake = FakeAdb()
        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="只观察", max_steps=2, allowed=("done",))
        with self.assertRaises(AdbError) as caught:
            session.execute(PhoneAction("launch", package="com.example.app"))
        self.assertEqual(caught.exception.code, "phone_action_forbidden")

    def test_step_budget_is_enforced(self):
        fake = ChangingAdb()
        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="反复按键", max_steps=2, allowed=("key", "tap_text"))
        outcome = session.run(lambda observation, history: PhoneAction("key", key="BACK"))
        self.assertEqual(outcome.state, "exhausted")
        self.assertEqual(len(outcome.steps), 2)


class ImeScheduler:
    def __init__(self):
        self.delays = []
        self.fires = []

    def __call__(self, delay, fire):
        self.delays.append(delay)
        self.fires.append(fire)


def reset_ime_state():
    from companion_v2.device import adb as module

    with module._IME_RESTORE_LOCK:
        for timer in module._IME_RESTORE_TIMERS.values():
            timer.cancel()
        module._IME_RESTORE_TIMERS.clear()
        module._IME_PENDING_RESTORE.clear()
        module._IME_RESTORE_ERRORS.clear()


class ImeTextInputTests(unittest.TestCase):
    LATIN = "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"

    def setUp(self):
        reset_ime_state()

    def client(self, fake=None):
        return AdbClient("adb.exe", serial="SER123", runner=fake or FakeAdb())

    def test_unicode_switches_broadcasts_and_restores_after_the_delay(self):
        fake = FakeAdb()
        scheduler = ImeScheduler()
        client = self.client(fake)
        detail = ImeTextInput(client, scheduler=scheduler).type("你好")
        self.assertIn("ADBKeyboard", detail)
        self.assertEqual(scheduler.delays, [30])
        calls = [call[1:] for call in fake.calls]
        self.assertIn(("-s", "SER123", "shell", "ime", "set", ADB_KEYBOARD_IME), calls)
        self.assertIn(("-s", "SER123", "shell", "am", "broadcast", "-a", "ADB_INPUT_B64",
                       "--es", "msg", "5L2g5aW9"), calls)
        sets = [call[-1] for call in calls if "ime" in call and "set" in call]
        self.assertNotIn(self.LATIN, sets)
        scheduler.fires[0]()
        sets = [call[-1] for call in [item[1:] for item in fake.calls]]
        self.assertIn(self.LATIN, sets)

    def test_a_second_type_reschedules_instead_of_restoring_early(self):
        fake = FakeAdb()
        scheduler = ImeScheduler()
        client = self.client(fake)
        input_method = ImeTextInput(client, scheduler=scheduler)
        input_method.type("第一段")
        input_method.type("第二段")
        self.assertEqual(scheduler.delays, [30, 30])
        scheduler.fires[-1]()
        calls = [call[1:] for call in fake.calls]
        self.assertIn(("-s", "SER123", "shell", "ime", "set", self.LATIN), calls)

    def test_missing_keyboard_is_not_installed_by_us(self):
        class NoKeyboard(FakeAdb):
            def __call__(self, args, timeout):
                if "ime" in args and "list" in args:
                    return subprocess.CompletedProcess(args, 0, b"com.other/.Ime\n", b"")
                return super().__call__(args, timeout)

        with self.assertRaises(AdbError) as caught:
            ImeTextInput(self.client(NoKeyboard()), scheduler=ImeScheduler()).type("你好")
        self.assertEqual(caught.exception.code, "adb_ime_missing")

    def test_a_failed_restore_is_recorded_for_the_next_observation(self):
        fake = FakeAdb()
        fake.failures[("-s", "SER123", "shell", "ime", "set", self.LATIN)] = "device busy"
        scheduler = ImeScheduler()
        client = self.client(fake)
        ImeTextInput(client, scheduler=scheduler).type("你好")
        scheduler.fires[0]()
        error = take_ime_restore_error(client)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "adb_ime_restore_failed")

    def test_no_switch_when_it_is_already_current(self):
        class Current(FakeAdb):
            def __call__(self, args, timeout):
                if "settings" in args and "default_input_method" in args:
                    return subprocess.CompletedProcess(args, 0, (ADB_KEYBOARD_IME + "\n").encode(), b"")
                return super().__call__(args, timeout)

        fake = Current()
        scheduler = ImeScheduler()
        ImeTextInput(self.client(fake), scheduler=scheduler).type("你好")
        self.assertEqual(scheduler.delays, [])
        calls = [call[1:] for call in fake.calls]
        self.assertFalse(any("set" in call for call in calls))


class UnicodeTypingTests(unittest.TestCase):
    def setUp(self):
        reset_ime_state()
        self.patcher = patch("companion_v2.device.adb._default_scheduler",
                             lambda delay, fire: None)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_session_routes_ascii_to_input_text_and_unicode_to_the_keyboard(self):
        fake = FakeAdb()
        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="输入", ime="adbkeyboard",
                               allowed=("type", "done", "fail"))
        session.execute(PhoneAction("type", text="hello world"))
        session.execute(PhoneAction("type", text="你好"))
        calls = [call[1:] for call in fake.calls]
        self.assertIn(("-s", "SER123", "shell", "input", "text", "hello%sworld"), calls)
        self.assertIn(("-s", "SER123", "shell", "am", "broadcast", "-a", "ADB_INPUT_B64",
                       "--es", "msg", "5L2g5aW9"), calls)

    def test_unicode_without_the_backend_is_refused(self):
        client = AdbClient("adb.exe", serial="SER123", runner=FakeAdb())
        session = PhoneSession(client, goal="输入", allowed=("type", "done", "fail"))
        with self.assertRaises(AdbError) as caught:
            session.execute(PhoneAction("type", text="你好"))
        self.assertEqual(caught.exception.code, "adb_text_unsupported")


if __name__ == "__main__":
    unittest.main()
