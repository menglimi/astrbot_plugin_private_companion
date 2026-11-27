from pathlib import Path
import tempfile
from unittest.mock import patch

from companion_v2.decision.store import (
    LayaSettingsStore,
    _clean_windows_path,
    _install_command,
    _remote_commands,
)


def test_laya_install_does_not_pull_a_second_torch_wheel():
    local = _install_command()
    remote, _ = _remote_commands()
    assert "install --no-deps" in local
    assert "install --no-deps" in remote
    assert "torch" not in local
    assert "transformers>=4.48.0" in local
    assert "fastapi>=0.110.0" in remote


def test_clean_windows_path_strips_extended_prefixes():
    assert _clean_windows_path(r"\\?\D:\AstrBot\backend\python\python.exe") == r"D:\AstrBot\backend\python\python.exe"
    assert _clean_windows_path(r"\\?\UNC\fileserver\share\python.exe") == r"\\fileserver\share\python.exe"
    assert _clean_windows_path(r"D:\AstrBot\python.exe") == r"D:\AstrBot\python.exe"
    assert _clean_windows_path("/usr/bin/python3") == "/usr/bin/python3"


def test_laya_commands_and_store_read_never_emit_extended_path_prefix():
    class _ExtendedPath:
        def __init__(self, text: str):
            self._text = text

        def resolve(self):
            return self

        def __str__(self):
            return self._text

    fake_exe = _ExtendedPath(r"\\?\D:\AstrBot\backend\python\python.exe")
    with patch("companion_v2.decision.store.Path", return_value=fake_exe):
        local = _install_command()
        remote_install, remote_start = _remote_commands()

    assert "\\\\?\\" not in local
    assert "\\\\?\\" not in remote_install
    assert "\\\\?\\" not in remote_start
    assert r"D:\AstrBot\backend\python\python.exe" in local
    assert r"D:\AstrBot\backend\python\python.exe" in remote_start

    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LayaSettingsStore(Path(tmp_dir))
        with (
            patch("companion_v2.decision.store._model_cache_dir", return_value=_ExtendedPath(r"\\?\D:\cache\hf")),
            patch("companion_v2.decision.store.cached_multilingual_model", return_value=None),
        ):
            snapshot = store.read()
    assert not snapshot["python_executable"].startswith("\\\\?\\")
    assert snapshot["model_cache_path"] == r"D:\cache\hf"
    assert "\\\\?\\" not in snapshot["install_command"]
    assert "\\\\?\\" not in snapshot["remote_install_command"]
    assert "\\\\?\\" not in snapshot["remote_start_command"]


def test_torch_install_command_never_emits_extended_path_prefix():
    from astrbot_host.laya_client import _torch_install_command

    class _ExtendedPath:
        def __init__(self, text: str):
            self._text = text

        def resolve(self):
            return self

        def __str__(self):
            return self._text

    fake_exe = _ExtendedPath(r"\\?\D:\AstrBot\backend\python\python.exe")
    with patch("astrbot_host.laya_client.Path", return_value=fake_exe):
        command = _torch_install_command()

    assert "\\\\?\\" not in command
    assert r"D:\AstrBot\backend\python\python.exe" in command
    assert "download.pytorch.org/whl/cu128" in command
