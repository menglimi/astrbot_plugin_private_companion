"""Cross-platform local folder opener with reliable Windows foreground focus.

When the plugin backend runs as a windowless sidecar process (for example,
launched by a desktop shell with ``CREATE_NO_WINDOW`` / ``SW_HIDE``), calling
bare ``os.startfile`` lets Windows open or reuse File Explorer behind the
active maximized browser/desktop window.  This helper normalizes extended
``\\\\?\\`` paths, ensures COM initialization on worker threads, verifies real
shell launch return codes, reuses already-open Explorer windows for the same
folder, and lifts the target Explorer window to the foreground.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time


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
SWP_SHOWWINDOW = 0x0040
FLASHW_ALL = 0x00000003
FLASHW_TIMERNOFG = 0x0000000C

_EXPLORER_WINDOW_CLASSES = frozenset({"CabinetWClass", "ExploreWClass"})
_FOREGROUND_WAIT_SECONDS = 1.2
_FOREGROUND_POLL_INTERVAL = 0.08


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


def _extract_address_path(text: str) -> str | None:
    """Extract a Windows path from Explorer's ToolbarWindow32 address label."""
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
    from ctypes import wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p

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

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    user32.FlashWindowEx.restype = wintypes.BOOL

    return ole32, shell32, user32, kernel32, FLASHWINFO, enum_proc_type


def _window_text(user32, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd) or 0)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_class(user32, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(128)
    copied = int(user32.GetClassNameW(hwnd, buffer, 128) or 0)
    return buffer.value[:copied]


def _list_explorer_windows(user32, enum_proc_type=None) -> list[tuple[int, str, str | None]]:
    """Return ``(hwnd, title, address_path)`` for open File Explorer windows."""
    if enum_proc_type is None:
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
    user32,
    clean_path: str,
    *,
    exclude_hwnds: set[int] | frozenset[int] = frozenset(),
    allow_any_new: bool = False,
    enum_proc_type=None,
) -> int | None:
    """Locate an open Explorer window matching ``clean_path`` (exact path first)."""
    target_norm = _normalize_compare_path(clean_path)
    basename = os.path.basename(clean_path.rstrip("\\/")).casefold()
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


def _bring_window_to_foreground(user32, kernel32, flash_cls, hwnd: int) -> bool:
    """Restore and lift an Explorer window above background/maximized callers."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)

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
        # Brief topmost toggle ensures the window rises above maximized WebView/browser
        # windows without staying pinned topmost afterward.
        user32.SetWindowPos(
            hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        user32.SetWindowPos(
            hwnd, ctypes.c_void_p(HWND_NOTOPMOST), 0, 0, 0, 0,
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
    # only launch failure (OSError) is fatal here.
    if proc is not None and proc.poll() is not None and proc.returncode not in (0, 1):
        raise OSError(f"explorer.exe exited immediately with code {proc.returncode}")


def _open_folder_windows(clean_path: str) -> None:
    ole32, shell32, user32, kernel32, flash_cls, enum_proc_type = _build_win32_api()
    com_hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    should_uninit = com_hr in (S_OK, S_FALSE)
    try:
        try:
            user32.AllowSetForegroundWindow(ASFW_ANY)
        except Exception:
            pass

        existing_hwnd = _find_explorer_window(user32, clean_path, enum_proc_type=enum_proc_type)
        if existing_hwnd:
            _bring_window_to_foreground(user32, kernel32, flash_cls, existing_hwnd)
            return

        try:
            before_hwnds = {hwnd for hwnd, _, _ in _list_explorer_windows(user32, enum_proc_type)}
        except Exception:
            before_hwnds = set()

        result = shell32.ShellExecuteW(None, "open", clean_path, None, None, SW_SHOWNORMAL)
        rc = int(result or 0)
        if rc <= 32:
            _spawn_explorer_fallback(clean_path)

        deadline = time.monotonic() + _FOREGROUND_WAIT_SECONDS
        hwnd = None
        while time.monotonic() < deadline:
            hwnd = _find_explorer_window(
                user32,
                clean_path,
                exclude_hwnds=before_hwnds,
                allow_any_new=True,
                enum_proc_type=enum_proc_type,
            )
            if hwnd:
                break
            time.sleep(_FOREGROUND_POLL_INTERVAL)
        if hwnd:
            _bring_window_to_foreground(user32, kernel32, flash_cls, hwnd)
    finally:
        if should_uninit:
            ole32.CoUninitialize()


def _open_folder_posix(clean_path: str) -> None:
    command = ["open", clean_path] if sys.platform == "darwin" else ["xdg-open", clean_path]
    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    if proc is not None:
        code = proc.poll()
        if code is not None and code != 0:
            raise OSError(f"{command[0]} exited immediately with code {code}")


def open_local_folder(target: str | os.PathLike[str]) -> str:
    """Open an existing local directory visibly in the OS file manager.

    Returns the normalized path that was opened. Raises ``OSError`` or
    ``ValueError`` if the directory does not exist or cannot be launched.
    """
    clean_path = clean_windows_path(target) if os.name == "nt" else os.fspath(target)
    if not isinstance(clean_path, str) or not clean_path.strip():
        raise ValueError("folder path must be a non-empty string")
    if not os.path.isdir(clean_path):
        raise FileNotFoundError(f"local folder does not exist: {clean_path}")
    if os.name == "nt":
        _open_folder_windows(clean_path)
    else:
        _open_folder_posix(clean_path)
    return clean_path
