"""Cross-platform local folder opener with Win11 foreground focus and headless safety.

All Win32 APIs (``ctypes.WinDLL``, ``STARTUPINFO``, process creation flags) are
strictly scoped to Windows runtime helpers so Linux, macOS, and container
deployments never incur platform-specific import or attribute errors.

Supported environments:
1. Windows interactive desktop: cleans ``\\\\?\\`` / ``\\\\?\\UNC\\`` prefixes,
   matches existing Windows 10/11 File Explorer windows via COM ``IShellWindows``
   and ``EnumWindows``, opens with explicit ``ShellExecuteW("open", ..., SW_SHOWNORMAL)``
   (checking ``> 32`` with ``explorer.exe`` fallback), restores minimized or
   off-screen windows into the active monitor work area, and raises the window
   to the foreground.
2. Windows non-interactive / Session 0 / alternate desktop: returns
   ``FolderOpenResult(opened=False, reason="non_interactive_desktop")`` instead
   of silently spawning an invisible Explorer window and claiming success.
3. macOS desktop: launches ``open <path>`` detached and checks immediate exit.
4. Linux desktop (``DISPLAY`` or ``WAYLAND_DISPLAY``): launches ``xdg-open``
   (falling back to ``gio open``) detached with ``DEVNULL`` and checks immediate
   exit.
5. Linux headless / Docker / SSH (no display server or no opener binary):
   returns ``FolderOpenResult(opened=False, reason="headless_environment")``
   without crashing or falsely claiming a GUI window was opened.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlparse


logger = logging.getLogger(__name__)

COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106 as signed 32-bit HRESULT

SW_SHOW = 5
SW_SHOWNORMAL = 1
SW_RESTORE = 9

ASFW_ANY = 0xFFFFFFFF
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
FLASHW_ALL = 0x00000003
FLASHW_TIMERNOFG = 0x0000000C
MONITOR_DEFAULTTONEAREST = 2
UOI_NAME = 2
DESKTOP_READOBJECTS = 0x0001
CLSCTX_LOCAL_SERVER = 4
VT_I4 = 3

_CLSID_SHELL_WINDOWS = "{9BA05972-F6A8-11CF-A442-00A0C90A8F39}"
_IID_ISHELL_WINDOWS = "{85CB6900-4D95-11CF-960C-0080C7F4EE85}"
_IID_IWEBBROWSER_APP = "{0002DF05-0000-0000-C000-000000000046}"

_EXPLORER_WINDOW_CLASSES = frozenset({"CabinetWClass", "ExploreWClass"})
_FOREGROUND_WAIT_SECONDS = 1.4
_FOREGROUND_POLL_INTERVAL = 0.08
_POSIX_IMMEDIATE_CHECK_SECONDS = 0.05


@dataclass(frozen=True)
class FolderOpenResult:
    path: str
    opened: bool
    reason: str = "opened"
    launcher: str = ""


def clean_windows_path(path: str | os.PathLike[str]) -> str:
    """Strip Windows extended-length ``\\\\?\\`` / ``\\\\?\\UNC\\`` prefixes."""
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise ValueError("path must be a string or path-like object")
    text = raw.strip()
    if not text:
        raise ValueError("path must not be empty")
    lower = text.lower()
    if lower.startswith("\\\\?\\unc\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normpath(text) if os.name == "nt" else text


def _normalize_compare_path(path: str) -> str:
    cleaned = clean_windows_path(path) if os.name == "nt" else os.path.normpath(path)
    return os.path.normcase(os.path.normpath(cleaned)).rstrip("\\/")


def _file_url_to_windows_path(url: str) -> str | None:
    """Convert an Explorer ``file:///...`` LocationURL into a Windows path."""
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "file":
        return None
    decoded_path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        unc = "\\\\" + unquote(parsed.netloc) + decoded_path.replace("/", "\\")
        return clean_windows_path(unc)
    if len(decoded_path) >= 3 and decoded_path[0] == "/" and decoded_path[2] == ":":
        decoded_path = decoded_path[1:]
    if not decoded_path:
        return None
    return clean_windows_path(decoded_path.replace("/", "\\"))


def _extract_address_path(text: str) -> str | None:
    """Extract a Windows path from legacy Explorer ToolbarWindow32 address text."""
    if not text:
        return None
    for separator in (": ", "："):
        if separator in text:
            candidate = text.split(separator, 1)[1].strip()
            if len(candidate) >= 2 and (candidate[1] == ":" or candidate.startswith("\\\\")):
                return candidate
    stripped = text.strip()
    if len(stripped) >= 2 and (stripped[1] == ":" or stripped.startswith("\\\\")):
        return stripped
    return None


def _build_win32_api():
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class VARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.WORD),
            ("r1", wintypes.WORD),
            ("r2", wintypes.WORD),
            ("r3", wintypes.WORD),
            ("val", ctypes.c_uint64),
            ("pad", ctypes.c_uint64),
        ]

    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
    oleaut32.SysFreeString.restype = None

    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    user32.GetProcessWindowStation.argtypes = []
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL

    user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user32.AllowSetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    if hasattr(user32, "SwitchToThisWindow"):
        user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        user32.SwitchToThisWindow.restype = None
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc_type, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL

    user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    user32.FlashWindowEx.restype = wintypes.BOOL

    return {
        "ole32": ole32,
        "oleaut32": oleaut32,
        "shell32": shell32,
        "user32": user32,
        "kernel32": kernel32,
        "GUID": GUID,
        "VARIANT": VARIANT,
        "RECT": RECT,
        "MONITORINFO": MONITORINFO,
        "FLASHWINFO": FLASHWINFO,
        "enum_proc_type": enum_proc_type,
    }


def _user_object_name(user32, handle) -> str | None:
    if not handle:
        return None
    import ctypes
    from ctypes import wintypes

    buffer = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if user32.GetUserObjectInformationW(handle, UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
        return buffer.value
    return None


def _windows_interactive_desktop_status(api) -> tuple[bool, str]:
    """Check whether the current Windows process runs on the visible interactive desktop."""
    user32 = api["user32"]
    kernel32 = api["kernel32"]
    try:
        winsta = _user_object_name(user32, user32.GetProcessWindowStation())
        thread_desktop = _user_object_name(user32, user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
        input_desktop = None
        h_input = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if h_input:
            try:
                input_desktop = _user_object_name(user32, h_input)
            finally:
                user32.CloseDesktop(h_input)
    except Exception:
        return True, "unknown"

    detail = f"winsta={winsta or '?'} thread_desktop={thread_desktop or '?'} input_desktop={input_desktop or '?'}"
    if winsta and winsta.casefold() != "winsta0":
        return False, detail
    if thread_desktop and thread_desktop.casefold() != "default":
        return False, detail
    if input_desktop and thread_desktop and input_desktop.casefold() != thread_desktop.casefold():
        return False, detail
    return True, detail


def _list_shell_windows(api) -> list[tuple[int, str]]:
    """Query COM ``IShellWindows`` for exact ``(hwnd, local_path)`` pairs on Win10/Win11."""
    import ctypes
    from ctypes import wintypes
    import uuid

    ole32 = api["ole32"]
    oleaut32 = api["oleaut32"]
    user32 = api["user32"]
    guid_cls = api["GUID"]
    variant_cls = api["VARIANT"]

    def make_guid(text: str):
        parsed = uuid.UUID(text)
        return guid_cls(
            parsed.time_low,
            parsed.time_mid,
            parsed.time_hi_version,
            (ctypes.c_ubyte * 8)(*parsed.bytes[8:]),
        )

    def vtbl_call(ptr, slot: int, restype, *argtypes):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[slot])

    clsid = make_guid(_CLSID_SHELL_WINDOWS)
    iid_sw = make_guid(_IID_ISHELL_WINDOWS)
    iid_wb = make_guid(_IID_IWEBBROWSER_APP)
    sw = ctypes.c_void_p()
    hr = int(ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_LOCAL_SERVER, ctypes.byref(iid_sw), ctypes.byref(sw)))
    if hr != 0 or not sw.value:
        return []

    results: list[tuple[int, str]] = []
    release_sw = vtbl_call(sw, 2, wintypes.ULONG)
    try:
        get_count = vtbl_call(sw, 7, ctypes.c_long, ctypes.POINTER(ctypes.c_long))
        item_fn = vtbl_call(sw, 8, ctypes.c_long, variant_cls, ctypes.POINTER(ctypes.c_void_p))
        count = ctypes.c_long(0)
        if int(get_count(sw, ctypes.byref(count))) != 0:
            return results
        for index in range(max(0, int(count.value))):
            var = variant_cls()
            var.vt = VT_I4
            var.val = index
            disp = ctypes.c_void_p()
            if int(item_fn(sw, var, ctypes.byref(disp))) != 0 or not disp.value:
                continue
            release_disp = vtbl_call(disp, 2, wintypes.ULONG)
            try:
                qi = vtbl_call(disp, 0, ctypes.c_long, ctypes.POINTER(guid_cls), ctypes.POINTER(ctypes.c_void_p))
                wb = ctypes.c_void_p()
                if int(qi(disp, ctypes.byref(iid_wb), ctypes.byref(wb))) != 0 or not wb.value:
                    continue
                release_wb = vtbl_call(wb, 2, wintypes.ULONG)
                try:
                    get_url = vtbl_call(wb, 30, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
                    get_hwnd = vtbl_call(wb, 37, ctypes.c_long, ctypes.POINTER(ctypes.c_ssize_t))
                    bstr_url = ctypes.c_void_p()
                    hwnd = ctypes.c_ssize_t(0)
                    get_url(wb, ctypes.byref(bstr_url))
                    get_hwnd(wb, ctypes.byref(hwnd))
                    try:
                        raw_url = ctypes.wstring_at(bstr_url.value) if bstr_url.value else ""
                    finally:
                        if bstr_url.value:
                            oleaut32.SysFreeString(bstr_url)
                    local_path = _file_url_to_windows_path(raw_url)
                    if hwnd.value and local_path and user32.IsWindow(int(hwnd.value)):
                        results.append((int(hwnd.value), local_path))
                finally:
                    release_wb(wb)
            finally:
                release_disp(disp)
    finally:
        release_sw(sw)
    return results


def _window_text(user32, hwnd: int) -> str:
    import ctypes

    length = int(user32.GetWindowTextLengthW(hwnd) or 0)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_class(user32, hwnd: int) -> str:
    import ctypes

    buffer = ctypes.create_unicode_buffer(128)
    copied = int(user32.GetClassNameW(hwnd, buffer, 128) or 0)
    return buffer.value[:copied]


def _list_explorer_windows(user32, enum_proc_type=None) -> list[tuple[int, str, str | None]]:
    """Return ``(hwnd, title, address_path)`` for open File Explorer windows on this desktop."""
    if enum_proc_type is None:
        import ctypes
        from ctypes import wintypes

        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    results: list[tuple[int, str, str | None]] = []

    def inspect_child(child_hwnd, _lparam):
        if _window_class(user32, child_hwnd) == "ToolbarWindow32":
            extracted = _extract_address_path(_window_text(user32, child_hwnd))
            if extracted:
                found_address[0] = extracted
                return False
        return True

    child_callback = enum_proc_type(inspect_child)

    def inspect_top(hwnd, _lparam):
        if _window_class(user32, hwnd) in _EXPLORER_WINDOW_CLASSES:
            found_address[0] = None
            try:
                user32.EnumChildWindows(hwnd, child_callback, 0)
            except Exception:
                pass
            results.append((int(hwnd), _window_text(user32, hwnd), found_address[0]))
        return True

    found_address: list[str | None] = [None]
    user32.EnumWindows(enum_proc_type(inspect_top), 0)
    return results


def _find_explorer_window(
    api,
    clean_path: str,
    *,
    exclude_hwnds: set[int] | frozenset[int] = frozenset(),
    allow_any_new: bool = False,
) -> int | None:
    """Locate an open Explorer window matching ``clean_path`` (IShellWindows exact path first)."""
    target_norm = _normalize_compare_path(clean_path)
    basename = os.path.basename(clean_path.rstrip("\\/")).casefold()

    try:
        for hwnd, shell_path in _list_shell_windows(api):
            if _normalize_compare_path(shell_path) == target_norm:
                return hwnd
    except Exception:
        pass

    user32 = api["user32"]
    enum_proc_type = api.get("enum_proc_type")
    try:
        windows = _list_explorer_windows(user32, enum_proc_type)
    except Exception:
        return None

    title_matches: list[int] = []
    new_windows: list[int] = []
    for hwnd, title, address in windows:
        if address and _normalize_compare_path(address) == target_norm:
            return hwnd
        is_new = hwnd not in exclude_hwnds
        if is_new:
            new_windows.append(hwnd)
        title_clean = title.strip()
        if title_clean:
            if _normalize_compare_path(title_clean) == target_norm:
                return hwnd
            title_lower = title_clean.casefold()
            if basename and (
                title_lower == basename
                or title_lower.startswith(basename + " - ")
                or title_lower.startswith(basename + " — ")
            ):
                if is_new:
                    title_matches.insert(0, hwnd)
                else:
                    title_matches.append(hwnd)

    if title_matches:
        return title_matches[0]
    if allow_any_new and new_windows:
        return new_windows[0]
    return None


def _ensure_window_on_screen(api, hwnd: int) -> bool:
    """Clamp an off-screen or unreachable Explorer window back inside the monitor work area."""
    import ctypes

    user32 = api["user32"]
    rect_cls = api.get("RECT")
    monitor_cls = api.get("MONITORINFO")
    if rect_cls is None or monitor_cls is None:
        return False
    try:
        rect = rect_cls()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return False
        info = monitor_cls()
        info.cbSize = ctypes.sizeof(monitor_cls)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return False
    except Exception:
        return False

    work = info.rcWork
    work_w = max(320, int(work.right - work.left))
    work_h = max(240, int(work.bottom - work.top))
    win_w = max(320, int(rect.right - rect.left))
    win_h = max(240, int(rect.bottom - rect.top))

    inter_w = max(0, min(int(rect.right), int(work.right)) - max(int(rect.left), int(work.left)))
    inter_h = max(0, min(int(rect.bottom), int(work.bottom)) - max(int(rect.top), int(work.top)))
    title_offscreen = int(rect.top) < int(work.top) - 8 or int(rect.top) > int(work.bottom) - 80
    mostly_offscreen = inter_w < min(240, win_w) or inter_h < min(120, win_h)
    if not title_offscreen and not mostly_offscreen:
        return False

    new_w = min(win_w, work_w)
    new_h = min(win_h, work_h)
    new_x = max(int(work.left), min(int(rect.left), int(work.right) - new_w))
    new_y = max(int(work.top), min(int(rect.top), int(work.bottom) - new_h))
    user32.SetWindowPos(
        hwnd,
        ctypes.c_void_p(0),
        new_x,
        new_y,
        new_w,
        new_h,
        SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )
    return True


def _bring_window_to_foreground(api, hwnd: int) -> bool:
    """Restore, clamp on-screen, and lift an Explorer window above background/maximized callers."""
    import ctypes

    user32 = api["user32"]
    kernel32 = api["kernel32"]
    flash_cls = api.get("FLASHWINFO")

    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)

    _ensure_window_on_screen(api, hwnd)

    fg_hwnd = int(user32.GetForegroundWindow() or 0)
    cur_tid = int(kernel32.GetCurrentThreadId() or 0)
    fg_tid = int(user32.GetWindowThreadProcessId(fg_hwnd, None) or 0) if fg_hwnd else 0
    target_tid = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)

    attached_fg = False
    attached_target = False
    activated = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if target_tid and target_tid != cur_tid and target_tid != fg_tid:
            attached_target = bool(user32.AttachThreadInput(cur_tid, target_tid, True))

        user32.BringWindowToTop(hwnd)
        activated = bool(user32.SetForegroundWindow(hwnd))
        if not activated and hasattr(user32, "SwitchToThisWindow"):
            try:
                user32.SwitchToThisWindow(hwnd, True)
                activated = int(user32.GetForegroundWindow() or 0) == hwnd
            except Exception:
                pass

        # Brief topmost toggle ensures the window rises above maximized WebView/browser
        # windows without staying pinned topmost afterward.
        user32.SetWindowPos(
            hwnd,
            ctypes.c_void_p(HWND_TOPMOST),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        user32.SetWindowPos(
            hwnd,
            ctypes.c_void_p(HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
    finally:
        if attached_target:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if attached_fg:
            user32.AttachThreadInput(cur_tid, fg_tid, False)

    if not activated and flash_cls is not None:
        try:
            info = flash_cls(
                cbSize=ctypes.sizeof(flash_cls),
                hwnd=hwnd,
                dwFlags=FLASHW_ALL | FLASHW_TIMERNOFG,
                uCount=3,
                dwTimeout=0,
            )
            user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass
    return activated


def _spawn_explorer_fallback(clean_path: str) -> None:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_SHOWNORMAL
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    proc = subprocess.Popen(
        ["explorer.exe", clean_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startupinfo,
        creationflags=creationflags,
        close_fds=True,
    )
    # explorer.exe hands off to the shell process and may exit 0 or 1 quickly;
    # only launch failure (OSError) or unexpected exit codes are fatal here.
    if proc is not None and proc.poll() is not None and proc.returncode not in (0, 1):
        raise OSError(f"explorer.exe exited immediately with code {proc.returncode}")


def _open_folder_windows(clean_path: str, *, _skip_desktop_check: bool = False) -> FolderOpenResult:
    api = _build_win32_api()
    ole32 = api["ole32"]
    shell32 = api["shell32"]
    user32 = api["user32"]

    if not _skip_desktop_check:
        interactive, detail = _windows_interactive_desktop_status(api)
        if not interactive:
            logger.warning(
                "[CompanionV2] 跳过打开本地目录：当前进程未运行在交互桌面 path=%s %s",
                clean_path,
                detail,
            )
            return FolderOpenResult(
                path=clean_path,
                opened=False,
                reason="non_interactive_desktop",
                launcher="none",
            )

    com_hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    should_uninit = com_hr in (S_OK, S_FALSE)
    try:
        try:
            user32.AllowSetForegroundWindow(ASFW_ANY)
        except Exception:
            pass

        existing_hwnd = _find_explorer_window(api, clean_path)
        if existing_hwnd:
            _bring_window_to_foreground(api, existing_hwnd)
            return FolderOpenResult(path=clean_path, opened=True, reason="reused", launcher="existing_explorer")

        try:
            before_hwnds = {hwnd for hwnd, _, _ in _list_explorer_windows(user32, api.get("enum_proc_type"))}
        except Exception:
            before_hwnds = set()

        result = shell32.ShellExecuteW(None, "open", clean_path, None, None, SW_SHOWNORMAL)
        rc = int(result or 0)
        launcher = "shell_execute"
        if rc <= 32:
            _spawn_explorer_fallback(clean_path)
            launcher = "explorer"

        deadline = time.monotonic() + _FOREGROUND_WAIT_SECONDS
        hwnd = None
        while time.monotonic() < deadline:
            hwnd = _find_explorer_window(
                api,
                clean_path,
                exclude_hwnds=before_hwnds,
                allow_any_new=True,
            )
            if hwnd:
                break
            time.sleep(_FOREGROUND_POLL_INTERVAL)
        if hwnd:
            _bring_window_to_foreground(api, hwnd)
        return FolderOpenResult(path=clean_path, opened=True, reason="opened", launcher=launcher)
    finally:
        if should_uninit:
            ole32.CoUninitialize()


def _check_posix_immediate_exit(proc, label: str) -> None:
    if proc is None:
        return
    code = proc.poll()
    if code is None and _POSIX_IMMEDIATE_CHECK_SECONDS > 0:
        time.sleep(_POSIX_IMMEDIATE_CHECK_SECONDS)
        code = proc.poll()
    if code is not None and code != 0:
        raise OSError(f"{label} exited immediately with code {code}")


def _open_folder_posix(clean_path: str) -> FolderOpenResult:
    if sys.platform == "darwin":
        opener = shutil.which("open")
        if not opener:
            return FolderOpenResult(
                path=clean_path,
                opened=False,
                reason="headless_environment",
                launcher="none",
            )
        proc = subprocess.Popen(
            [opener, clean_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        _check_posix_immediate_exit(proc, "open")
        return FolderOpenResult(path=clean_path, opened=True, reason="opened", launcher="open")

    has_display = bool(
        (os.environ.get("DISPLAY") or "").strip()
        or (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    )
    if not has_display:
        return FolderOpenResult(
            path=clean_path,
            opened=False,
            reason="headless_environment",
            launcher="none",
        )

    xdg_open = shutil.which("xdg-open")
    if xdg_open:
        command = [xdg_open, clean_path]
        launcher = "xdg-open"
    else:
        gio = shutil.which("gio")
        if not gio:
            return FolderOpenResult(
                path=clean_path,
                opened=False,
                reason="headless_environment",
                launcher="none",
            )
        command = [gio, "open", clean_path]
        launcher = "gio"

    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    _check_posix_immediate_exit(proc, launcher)
    return FolderOpenResult(path=clean_path, opened=True, reason="opened", launcher=launcher)


def open_local_folder(target: str | os.PathLike[str]) -> FolderOpenResult:
    """Open an existing local directory visibly in the OS file manager when a desktop exists.

    Returns a ``FolderOpenResult`` describing whether a desktop file manager was
    launched (``opened=True``) or skipped gracefully on a headless/non-interactive
    host (``opened=False``, with ``reason`` explaining why). Raises ``OSError`` or
    ``ValueError`` when the directory is missing or launching a present desktop
    opener fails.
    """
    clean_path = clean_windows_path(target) if os.name == "nt" else os.fspath(target)
    if not isinstance(clean_path, str) or not clean_path.strip():
        raise ValueError("folder path must be a non-empty string")
    if not os.path.isdir(clean_path):
        raise FileNotFoundError(f"local folder does not exist: {clean_path}")
    if os.name == "nt":
        return _open_folder_windows(clean_path)
    if os.name == "posix":
        return _open_folder_posix(clean_path)
    return FolderOpenResult(
        path=clean_path,
        opened=False,
        reason="unsupported_platform",
        launcher="none",
    )
