from companion_v2.decision.store import _install_command, _remote_commands


def test_laya_install_does_not_pull_a_second_torch_wheel():
    local = _install_command()
    remote, _ = _remote_commands()
    assert "install --no-deps" in local
    assert "install --no-deps" in remote
    assert "torch" not in local
    assert "transformers>=4.48.0" in local
    assert "fastapi>=0.110.0" in remote
