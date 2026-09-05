from __future__ import annotations

from pathlib import Path

from external_memory_dependency import ENV_NAME, resolve_memory_plugin_root


def _make_memory_checkout(root: Path) -> Path:
    bridge = root / "core" / "bridge.py"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("# real test checkout marker\n", encoding="utf-8")
    return root


def test_explicit_memory_root_is_used_when_it_has_real_bridge(tmp_path):
    root = _make_memory_checkout(tmp_path / "memory-plugin")
    result = resolve_memory_plugin_root(tmp_path / "companion", configured_root=root, environ={})
    assert result.root == root.resolve()
    assert "configured" in result.detail


def test_invalid_explicit_memory_root_does_not_fall_back(tmp_path):
    result = resolve_memory_plugin_root(
        tmp_path / "companion",
        configured_root=tmp_path / "missing",
        environ={},
    )
    assert result.root is None
    assert "invalid" in result.detail
    assert "core" in result.detail and "bridge.py" in result.detail


def test_environment_can_configure_memory_root(tmp_path):
    root = _make_memory_checkout(tmp_path / "memory-plugin")
    result = resolve_memory_plugin_root(
        tmp_path / "companion",
        environ={ENV_NAME: str(root)},
    )
    assert result.root == root.resolve()


def test_absent_optional_checkout_has_actionable_reason(tmp_path):
    companion = tmp_path / "workspace" / "project" / "companion"
    companion.mkdir(parents=True)
    result = resolve_memory_plugin_root(companion, environ={})
    assert result.root is None
    assert ENV_NAME in result.detail
    assert "--memory-plugin-root" in result.detail
