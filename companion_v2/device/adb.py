"""A small, bounded ADB client for an authorized developer-mode phone.

This module owns transport only.  It never decides what to do, never stores
conversation data and never exposes a general shell: every command is one of the
declared operations below, each with its own timeout and output cap.  The
decision layer (``session.py``) and the host permission layer sit above it.

The executable is discovered from an explicit path, ``ANDROID_HOME`` /
``ANDROID_SDK_ROOT``, the default Windows SDK location, or ``PATH``.  Tests
inject a ``runner`` so no real device or binary is required.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Sequence

DEFAULT_TIMEOUT = 20.0
SCREENSHOT_TIMEOUT = 30.0
MAX_OUTPUT_BYTES = 1 << 20
MAX_SCREENSHOT_BYTES = 24 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_UI_DUMP_FALLBACK_PATH = "/data/local/tmp/companion-window.xml"
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
ADB_KEYBOARD_PACKAGE = "com.android.adbkeyboard"
_IME_ID = re.compile(r"^[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+$")
KEY_EVENTS = frozenset({
    "HOME", "BACK", "RECENTS", "APP_SWITCH", "MENU", "ENTER", "DEL", "SPACE",
    "TAB", "WAKEUP", "VOLUME_UP", "VOLUME_DOWN", "MUTE",
})
_DEVICES_HEADER = "List of devices attached"
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class AdbError(RuntimeError):
    """One declared ADB operation failed; ``code`` is stable for callers."""

    def __init__(self, code: str, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr[:2000]


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    transport_id: str = ""


@dataclass(frozen=True)
class UiElement:
    """One node of ``uiautomator dump`` with its tap center and identity."""

    text: str
    description: str
    package: str
    class_name: str
    clickable: bool
    enabled: bool
    bounds: tuple[int, int, int, int]
    index: int = 0

    @property
    def label(self) -> str:
        return self.text or self.description

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    def document(self) -> dict:
        return {"index": self.index, "text": self.text, "description": self.description,
                "package": self.package, "class": self.class_name,
                "clickable": self.clickable, "enabled": self.enabled,
                "bounds": list(self.bounds), "center": list(self.center)}


def _default_runner(args: Sequence[str], timeout: float, *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        list(args), input=stdin, capture_output=True, timeout=timeout, check=False,
        creationflags=flags,
    )


def diagnose_adb_not_found(explicit: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Classify why ADB could not be resolved without leaking environment details."""
    raw = str(explicit or "").strip()
    if raw:
        configured = Path(raw).expanduser()
        if configured.is_dir():
            reason = "explicit_path_is_dir"
        elif not configured.exists():
            reason = "explicit_path_missing"
        elif not configured.is_file() or (os.name != "nt" and not os.access(configured, os.X_OK)):
            reason = "explicit_path_not_executable"
        else:
            reason = "explicit_path_missing"
        return {"reason": reason, "configured_adb_path": raw}
    return {"reason": "auto_discovery_empty", "configured_adb_path": ""}


def discover_adb(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the ADB executable or fail with one actionable code."""
    candidates: list[Path] = []
    if explicit and str(explicit).strip():
        # An explicit path is an operator decision; silently falling back to a
        # different ADB build would make the device run against an unintended one.
        configured = Path(str(explicit).strip()).expanduser()
        if configured.is_dir():
            raise AdbError("adb_not_found", "the configured adb path is a directory; point to the adb executable")
        if not configured.is_file():
            raise AdbError("adb_not_found", "the configured adb executable does not exist")
        if os.name != "nt" and not os.access(configured, os.X_OK):
            raise AdbError("adb_not_found", "the configured adb path is not executable")
        return configured
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
            candidates.append(Path(local) / "Android" / "platform-tools" / "adb.exe")
    found = shutil.which("adb")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AdbError("adb_not_found",
                   "adb executable was not found; set an explicit path or install Android platform-tools")


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def parse_devices(output: str) -> tuple[AdbDevice, ...]:
    """Parse ``adb devices -l``; daemon banners and the header are ignored."""
    devices = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*") or stripped.startswith(_DEVICES_HEADER):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        fields = {}
        for part in parts[2:]:
            key, separator, value = part.partition(":")
            if separator:
                fields[key] = value
        devices.append(AdbDevice(serial, state, fields.get("model", ""),
                                 fields.get("product", ""), fields.get("transport_id", "")))
    return tuple(devices)


def parse_ui_hierarchy(document: str) -> tuple[UiElement, ...]:
    """Parse the XML dump; malformed nodes are skipped instead of guessed."""
    from xml.etree import ElementTree

    start = document.find("<?xml")
    if start < 0:
        start = document.find("<hierarchy")
    end = document.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise AdbError("adb_ui_invalid", "uiautomator returned no hierarchy document")
    try:
        root = ElementTree.fromstring(document[start:end + len("</hierarchy>")])
    except ElementTree.ParseError as error:
        raise AdbError("adb_ui_invalid", "uiautomator hierarchy is not valid XML") from error
    elements: list[UiElement] = []
    for node in root.iter("node"):
        match = _BOUNDS.search(node.attrib.get("bounds", ""))
        if match is None:
            continue
        bounds = tuple(int(value) for value in match.groups())
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        elements.append(UiElement(
            text=(node.attrib.get("text") or "").strip()[:200],
            description=(node.attrib.get("content-desc") or "").strip()[:200],
            package=(node.attrib.get("package") or "").strip()[:160],
            class_name=(node.attrib.get("class") or "").strip()[:160],
            clickable=node.attrib.get("clickable") == "true",
            enabled=node.attrib.get("enabled") != "false",
            bounds=bounds, index=len(elements),
        ))
    return tuple(elements)


def select_elements(elements: Sequence[UiElement], text: str, *, limit: int = 12) -> tuple[UiElement, ...]:
    """Rank visible nodes by exact label, then clickable label, then containment."""
    needle = text.strip().casefold()
    if not needle:
        return ()
    exact, clickable, partial = [], [], []
    for element in elements:
        label = element.label.casefold()
        if not label:
            continue
        if label == needle:
            (clickable if element.clickable else exact).append(element)
        elif needle in label and (element.clickable or element.text or element.description):
            partial.append(element)
    ranked = sorted(clickable, key=lambda item: item.bounds[2] - item.bounds[0]) + exact + partial
    return tuple(ranked[:max(1, limit)])


class AdbClient:
    """One executable and one selected serial; every call is bounded."""

    def __init__(self, executable: str | os.PathLike[str], *, serial: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 runner: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self.executable = Path(executable)
        self.serial = (serial or "").strip() or None
        if not 1 <= timeout <= 120:
            raise ValueError("adb timeout must be between 1 and 120 seconds")
        self.timeout = float(timeout)
        self._runner = runner or _default_runner
        self._screen_size: tuple[int, int] | None = None

    # -- low level -----------------------------------------------------

    def _execute(self, args: Sequence[str], *, timeout: float | None = None,
                 stdin: bytes | None = None) -> subprocess.CompletedProcess:
        command = [str(self.executable), *args]
        try:
            options = {"stdin": stdin} if stdin is not None else {}
            return self._runner(command, float(timeout or self.timeout), **options)
        except subprocess.TimeoutExpired as error:
            raise AdbError("adb_timeout", "the device did not answer before the deadline") from error
        except OSError as error:
            raise AdbError("adb_unavailable", "the ADB process could not be started") from error

    def _check(self, result: subprocess.CompletedProcess, *, code: str = "adb_failed") -> str:
        output = _text(result.stdout)
        error = _text(result.stderr)
        if result.returncode != 0:
            raise AdbError(code, (error or output or "adb returned a failure").strip()[:500], stderr=error)
        if len(output.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES:
            raise AdbError("adb_output_too_large", "adb output exceeded the bounded size")
        return output

    def _device(self, *args: str, timeout: float | None = None, binary: bool = False) -> subprocess.CompletedProcess:
        serial = self.serial or self._single_serial()
        command = [*(["-s", serial] if serial else []), *args]
        result = self._execute(command, timeout=timeout)
        if binary:
            return result
        return result

    def _single_serial(self) -> str:
        devices = self.devices()
        ready = [device for device in devices if device.state == "device"]
        if not ready:
            raise AdbError("adb_no_device", "no authorized device is connected")
        if len(ready) > 1:
            raise AdbError("adb_multiple_devices", "more than one device is connected; select a serial first")
        self.serial = ready[0].serial
        return self.serial

    # -- device set ----------------------------------------------------

    def devices(self) -> tuple[AdbDevice, ...]:
        result = self._execute(["devices", "-l"])
        return parse_devices(self._check(result, code="adb_devices_failed"))

    def connect(self, address: str) -> str:
        host, separator, port = address.partition(":")
        if not separator or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise AdbError("adb_address_invalid", "wireless debugging requires host:port")
        result = self._execute(["connect", address], timeout=SCREENSHOT_TIMEOUT)
        output = self._check(result, code="adb_connect_failed")
        lowered = output.casefold()
        if "failed" in lowered or "cannot" in lowered or "unable" in lowered:
            raise AdbError("adb_connect_failed", output.strip()[:300] or "adb could not connect")
        return output.strip()[:300]

    def pair(self, address: str, code: str) -> str:
        host, separator, port = address.partition(":")
        if not separator or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise AdbError("adb_address_invalid", "pairing requires host:port")
        if not re.fullmatch(r"[0-9]{6}", (code or "").strip()):
            raise AdbError("adb_pair_code_invalid", "the wireless debugging pairing code is six digits")
        result = self._execute(["pair", address], timeout=SCREENSHOT_TIMEOUT,
                               stdin=(code.strip() + "\n").encode("ascii"))
        output = self._check(result, code="adb_pair_failed")
        if "successfully paired" not in output.casefold():
            raise AdbError("adb_pair_failed", output.strip()[:300] or "pairing did not succeed")
        return output.strip()[:300]

    # -- bounded device operations -------------------------------------

    def shell(self, *args: str, timeout: float | None = None) -> str:
        if not args:
            raise AdbError("adb_shell_empty", "one declared shell operation is required")
        result = self._device("shell", *args, timeout=timeout)
        return self._check(result)

    def screen_size(self) -> tuple[int, int]:
        if self._screen_size is not None:
            return self._screen_size
        output = self.shell("wm", "size")
        match = re.search(r"(\d+)x(\d+)", output)
        if match is None:
            raise AdbError("adb_screen_unknown", "the device did not report its screen size")
        self._screen_size = (int(match.group(1)), int(match.group(2)))
        return self._screen_size

    def screenshot(self) -> bytes:
        """Return one PNG original; never a path the caller can swap afterwards."""
        result = self._device("exec-out", "screencap", "-p", timeout=SCREENSHOT_TIMEOUT, binary=True)
        data = result.stdout if isinstance(result.stdout, bytes) else _text(result.stdout).encode("utf-8")
        error = _text(result.stderr)
        lowered_error = error.casefold()
        if (result.returncode == 0 and not data) or any(
            marker in lowered_error for marker in ("flag_secure", "secure", "protected", "permission denied")
        ):
            raise AdbError(
                "adb_screenshot_secure",
                (error or "current screen is protected by FLAG_SECURE and cannot be captured").strip()[:300],
                stderr=error,
            )
        if result.returncode != 0 or not data.startswith(PNG_SIGNATURE):
            raise AdbError("adb_screenshot_failed", (error or "screencap did not return a PNG").strip()[:300],
                           stderr=error)
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise AdbError("adb_screenshot_too_large", "screencap exceeded the bounded size")
        return data

    def ui_dump(self) -> tuple[UiElement, ...]:
        """Prefer the compressed in-memory dump; fall back to bounded tmp files."""
        document = ""
        last_error = ""
        for arguments in (("dump", "--compressed", "/dev/tty"), ("dump", "/dev/tty")):
            result = self._device("exec-out", "uiautomator", *arguments, timeout=SCREENSHOT_TIMEOUT)
            if result.returncode == 0:
                document = _text(result.stdout)
                if "<hierarchy" in document:
                    break
            else:
                last_error = (_text(result.stderr) or _text(result.stdout)).strip()
        if "<hierarchy" not in document:
            fallback = self._device("shell", "uiautomator", "dump", _UI_DUMP_FALLBACK_PATH,
                                    timeout=SCREENSHOT_TIMEOUT)
            if fallback.returncode != 0:
                detail = (_text(fallback.stderr) or _text(fallback.stdout) or last_error).strip()[:300]
                raise AdbError(
                    "adb_ui_failed",
                    detail or "uiautomator could not dump the current window",
                    stderr=_text(fallback.stderr),
                )
            read = self._device("exec-out", "cat", _UI_DUMP_FALLBACK_PATH, timeout=SCREENSHOT_TIMEOUT)
            document = _check_text(read)
        return parse_ui_hierarchy(document)

    def display_state(self) -> dict:
        """Read both signals: ``mWakefulness`` alone lies on always-on displays."""
        output = self.shell("dumpsys", "power", timeout=SCREENSHOT_TIMEOUT)
        wake = re.search(r"mWakefulness=(\w+)", output)
        display = (re.search(r"Display Power:\s*state=(\w+)", output)
                   or re.search(r"mScreenState=(\w+)", output))
        wakefulness = wake.group(1).casefold() if wake else ""
        display_power = display.group(1).casefold() if display else ""
        if display_power:
            on = display_power in ("on", "bright")
        elif wakefulness:
            on = wakefulness in ("awake", "dreaming")
        else:
            on = None
        return {"wakefulness": wakefulness, "display": display_power, "on": on}

    def ensure_awake(self) -> dict:
        """Wake with KEYCODE_WAKEUP, never POWER (POWER toggles a screen that is on)."""
        state = self.display_state()
        if state["on"] is False:
            self.shell("input", "keyevent", "WAKEUP")
            time.sleep(0.6)
            state = self.display_state()
            if state["on"] is False:
                raise AdbError("adb_screen_off", "the screen did not wake before the operation")
        return state

    def _keyguard(self) -> tuple[str, str]:
        policy = self.shell("dumpsys", "window", "policy", timeout=SCREENSHOT_TIMEOUT)
        delegate = policy.partition("KeyguardServiceDelegate")[2]
        showing = (re.search(r"^\s*mIsShowing=(true|false)\b", delegate, re.M)
                   or re.search(r"^\s*showing=(true|false)\b", delegate, re.M))
        secure = re.search(r"^\s*secure=(true|false)\b", delegate, re.M)
        return (showing.group(1) if showing else "", secure.group(1) if secure else "")

    def require_readable_screen(self) -> None:
        """Check the current screen without waking or dismissing the keyguard."""
        display = self.display_state()
        if display["on"] is False or display["wakefulness"] in ("asleep", "dozing"):
            raise AdbError("adb_screen_off", "the phone screen is off")
        if self._keyguard()[0] == "true":
            raise AdbError("adb_unlock_required", "the phone screen is locked")

    def ensure_interactive(self) -> None:
        """Wake the display and dismiss a confirmed swipe-only lock screen."""
        state = self.ensure_awake()
        showing, secure = self._keyguard()
        if showing != "true":
            return
        if secure == "false" and state["on"] is True:
            width, height = self.screen_size()
            self.swipe(width // 2, height * 85 // 100, width // 2, height * 24 // 100, duration_ms=380)
            time.sleep(0.7)
            if self._keyguard()[0] == "false":
                return
        raise AdbError("adb_unlock_required", "unlock the phone before continuing")

    def tap(self, x: int, y: int) -> None:
        width, height = self.screen_size()
        if not (0 <= x < width and 0 <= y < height):
            raise AdbError("adb_tap_out_of_bounds", "tap coordinates are outside the reported screen")
        self.shell("input", "tap", str(int(x)), str(int(y)))

    def tap_element(self, element: UiElement) -> None:
        x, y = element.center
        self.tap(x, y)

    def tap_text(self, text: str) -> UiElement:
        matches = select_elements(self.ui_dump(), text)
        if not matches:
            raise AdbError("adb_text_not_found", "no visible node matches the requested text")
        element = next((item for item in matches if item.clickable), matches[0])
        self.tap_element(element)
        return element

    def swipe(self, x1: int, y1: int, x2: int, y2: int, *, duration_ms: int = 300) -> None:
        if not 50 <= duration_ms <= 5000:
            raise AdbError("adb_swipe_invalid", "swipe duration must be between 50 and 5000 ms")
        self.shell("input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms)))

    def text(self, value: str) -> None:
        """Type printable ASCII; non-ASCII needs an input-method helper we do not install."""
        if not value:
            raise AdbError("adb_text_empty", "typed text cannot be empty")
        if len(value) > 500:
            raise AdbError("adb_text_too_long", "typed text exceeds the bounded length")
        if any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise AdbError("adb_text_unsupported", "ADB text input supports printable ASCII only")
        encoded = value.replace("%", "").replace(" ", "%s")
        self.shell("input", "text", encoded)

    # -- input method (ADBKeyboard) -----------------------------------

    def ime_list(self) -> tuple[str, ...]:
        output = self.shell("ime", "list", "-s", timeout=SCREENSHOT_TIMEOUT)
        return tuple(line.strip() for line in output.splitlines()
                     if _IME_ID.match(line.strip()))

    def ime_current(self) -> str:
        output = self.shell("settings", "get", "secure", "default_input_method",
                            timeout=SCREENSHOT_TIMEOUT).strip().strip('"')
        if output.casefold() in ("null", "none", ""):
            return ""
        if not _IME_ID.match(output):
            raise AdbError("adb_ime_unreadable", "the device did not report a readable current input method")
        return output

    def ime_set(self, ime_id: str) -> None:
        value = (ime_id or "").strip()
        if not _IME_ID.match(value):
            raise AdbError("adb_ime_invalid", "an input method id must be package/class")
        output = self.shell("ime", "set", value, timeout=SCREENSHOT_TIMEOUT)
        lowered = output.casefold()
        if "error" in lowered or "not found" in lowered or "unknown" in lowered:
            raise AdbError("adb_ime_set_failed", output.strip()[:300] or "the input method was refused")

    def key(self, name: str) -> None:
        value = (name or "").strip().upper()
        if value not in KEY_EVENTS:
            raise AdbError("adb_key_unknown", "the key is not in the declared key set")
        self.shell("input", "keyevent", value)

    def launch(self, package: str) -> str:
        package = (package or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+", package):
            raise AdbError("adb_package_invalid", "a full package name is required")
        output = self.shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1",
                            timeout=SCREENSHOT_TIMEOUT)
        if "No activities found" in output or "monkey aborted" in output.casefold():
            raise AdbError("adb_launch_failed", "the package has no launchable activity")
        return output.strip()[:300]

    def force_stop(self, package: str) -> str:
        """Only for an app that holds its own screen lock and cannot be left normally.

        The operator must add ``force_stop`` to the phone allowlist explicitly; a
        normal step can never reach it by default.
        """
        package = (package or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+", package):
            raise AdbError("adb_package_invalid", "a full package name is required")
        self.shell("am", "force-stop", package, timeout=SCREENSHOT_TIMEOUT)
        return f"已强制停止 {package}"

    def wait_text(self, text: str, *, timeout: float = 10.0, interval: float = 0.5) -> UiElement:
        deadline = time.monotonic() + max(0.5, min(60.0, timeout))
        while True:
            matches = select_elements(self.ui_dump(), text)
            if matches:
                return matches[0]
            if time.monotonic() >= deadline:
                raise AdbError("adb_wait_timeout", "the requested text did not appear before the deadline")
            time.sleep(max(0.1, min(2.0, interval)))

    def snapshot(self) -> dict:
        """A bounded observation: screen size, visible nodes and a PNG digest."""
        import hashlib

        nodes = self.ui_dump()
        image = self.screenshot()
        width, height = self.screen_size()
        return {"width": width, "height": height,
                "screenshot_sha256": hashlib.sha256(image).hexdigest(),
                "screenshot_bytes": len(image),
                "elements": [node.document() for node in nodes[:60]],
                "truncated": len(nodes) > 60}


def _check_text(result: subprocess.CompletedProcess) -> str:
    output = _text(result.stdout)
    if result.returncode != 0:
        raise AdbError("adb_ui_failed", (_text(result.stderr) or "reading the UI dump failed").strip()[:300])
    return output


_IME_RESTORE_LOCK = threading.Lock()
_IME_RESTORE_TIMERS: dict[tuple[str, str], threading.Timer] = {}
_IME_RESTORE_TOKENS: dict[tuple[str, str], object] = {}
_IME_PENDING_RESTORE: dict[tuple[str, str], str] = {}
_IME_RESTORE_ERRORS: dict[tuple[str, str], AdbError] = {}


def _ime_key(client: "AdbClient") -> tuple[str, str]:
    return (str(client.executable), client.serial or "")


def _restore_input_method(client: "AdbClient", previous: str) -> None:
    try:
        client.ime_set(previous)
    except AdbError as error:
        with _IME_RESTORE_LOCK:
            _IME_RESTORE_ERRORS[_ime_key(client)] = AdbError(
                "adb_ime_restore_failed",
                "the phone is left on ADBKeyboard; restore the previous input method manually",
            )
        return


def _default_scheduler(delay: float, fire) -> threading.Timer:
    timer = threading.Timer(delay, fire)
    timer.daemon = True
    timer.start()
    return timer


def schedule_ime_restore(client: "AdbClient", previous: str, delay: float, *, scheduler=None) -> None:
    """Restore later: switching back immediately drops characters still in the IME."""
    if not previous or previous == ADB_KEYBOARD_IME:
        return
    key = _ime_key(client)
    token = object()

    def fire():
        with _IME_RESTORE_LOCK:
            if _IME_RESTORE_TOKENS.get(key) is not token:
                return
            _IME_RESTORE_TOKENS.pop(key, None)
            _IME_RESTORE_TIMERS.pop(key, None)
            _IME_PENDING_RESTORE.pop(key, None)
        _restore_input_method(client, previous)

    with _IME_RESTORE_LOCK:
        old = _IME_RESTORE_TIMERS.pop(key, None)
        if old is not None:
            old.cancel()
        _IME_RESTORE_TOKENS[key] = token
        _IME_PENDING_RESTORE[key] = previous
    handle = (scheduler or _default_scheduler)(float(delay), fire)
    if handle is not None and callable(getattr(handle, "cancel", None)):
        with _IME_RESTORE_LOCK:
            if _IME_RESTORE_TOKENS.get(key) is token:
                _IME_RESTORE_TIMERS[key] = handle
            else:
                handle.cancel()


def take_ime_restore_error(client: "AdbClient") -> AdbError | None:
    """A restore that failed in its timer is surfaced on the next observation."""
    with _IME_RESTORE_LOCK:
        return _IME_RESTORE_ERRORS.pop(_ime_key(client), None)


class ImeTextInput:
    """Unicode text through ADBKeyboard, restoring the previous IME after a delay.

    ADBKeyboard is a separate APK the operator installs once; this class never
    installs or enables anything.  Switching back immediately after the
    broadcast is known to drop characters still being committed, so the restore
    is scheduled ``restore_seconds`` later (default 30).  A failed restore is
    recorded and reported on the next observation instead of being swallowed.
    """

    def __init__(self, client: AdbClient, *, ime_id: str = ADB_KEYBOARD_IME,
                 restore_seconds: int = 30, scheduler=None) -> None:
        if not _IME_ID.match(ime_id or ""):
            raise ValueError("an input method id must be package/class")
        if type(restore_seconds) is not int or not 5 <= restore_seconds <= 120:
            raise ValueError("IME restore delay must be between 5 and 120 seconds")
        self.client = client
        self.ime_id = ime_id
        self.restore_seconds = restore_seconds
        self._scheduler = scheduler

    def available(self) -> bool:
        return self.ime_id in self.client.ime_list()

    def type(self, value: str, *, clear_first: bool = False) -> str:
        import base64

        if not value:
            raise AdbError("adb_text_empty", "typed text cannot be empty")
        if len(value) > 500:
            raise AdbError("adb_text_too_long", "typed text exceeds the bounded length")
        if self.ime_id not in self.client.ime_list():
            raise AdbError("adb_ime_missing",
                           "ADBKeyboard is not installed or not registered as an input method")
        with _IME_RESTORE_LOCK:
            pending = _IME_PENDING_RESTORE.get(_ime_key(self.client))
        previous = pending or self.client.ime_current()
        switched = previous != self.ime_id
        try:
            if switched:
                self.client.ime_set(self.ime_id)
            if clear_first:
                self.client.shell("am", "broadcast", "-a", "ADB_CLEAR_TEXT", timeout=SCREENSHOT_TIMEOUT)
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            self.client.shell("am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded,
                              timeout=SCREENSHOT_TIMEOUT)
        except AdbError:
            # Nothing committed, so there is no character loss to wait for.
            if switched and previous:
                try:
                    self.client.ime_set(previous)
                except AdbError as restore_error:
                    raise AdbError(
                        "adb_ime_restore_failed",
                        "the phone is left on ADBKeyboard; restore the previous input method manually",
                    ) from restore_error
            raise
        schedule_ime_restore(self.client, previous, self.restore_seconds, scheduler=self._scheduler)
        return f"输入 {len(value)} 个字符（ADBKeyboard）"
