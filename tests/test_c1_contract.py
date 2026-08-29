from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path


COMPANION_ROOT = Path(__file__).resolve().parents[1]
PEIBAN_ROOT = COMPANION_ROOT.parents[1]
CONTRACT_PATH = COMPANION_ROOT / "bot_personal_contract.py"
_MEMORY_CONTRACT_CANDIDATES = (
    Path(os.environ["ASTRBOT_MEMORY_PLUGIN_ROOT"]) / "core" / "bot_personal_contract.py"
    if os.environ.get("ASTRBOT_MEMORY_PLUGIN_ROOT")
    else COMPANION_ROOT / ".missing-memory-root" / "core" / "bot_personal_contract.py",
    COMPANION_ROOT.parent / "memory" / "core" / "bot_personal_contract.py",
    PEIBAN_ROOT / "astrbot_plugin_memory_companion-main" / "core" / "bot_personal_contract.py",
    COMPANION_ROOT.parent / "astrbot_plugin_remember_you" / "core" / "bot_personal_contract.py",
    COMPANION_ROOT.parent / "我会牢牢记住你" / "core" / "bot_personal_contract.py",
)
MEMORY_CONTRACT_PATH = next((path for path in _MEMORY_CONTRACT_CANDIDATES if path.is_file()), _MEMORY_CONTRACT_CANDIDATES[0])
_SHARED_CONTRACT_CANDIDATES = (
    PEIBAN_ROOT / "doc" / "shared" / "bot_personal_contract.py",
    MEMORY_CONTRACT_PATH,
)
SHARED_CONTRACT_PATH = next((path for path in _SHARED_CONTRACT_CANDIDATES if path.is_file()), _SHARED_CONTRACT_CANDIDATES[0])


def load_contract(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load_contract(CONTRACT_PATH, "companion_bot_personal_contract")
memory_contract = load_contract(MEMORY_CONTRACT_PATH, "memory_bot_personal_contract")


def test_contract_bytes_match_authority_and_other_side():
    expected = SHARED_CONTRACT_PATH.read_bytes()
    assert CONTRACT_PATH.read_bytes() == expected
    assert MEMORY_CONTRACT_PATH.read_bytes() == expected
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).digest() == hashlib.sha256(
        MEMORY_CONTRACT_PATH.read_bytes()
    ).digest()


def test_frozen_contract_values_and_self_check():
    for module in (contract, memory_contract):
        assert module.CONTRACT_FINGERPRINT == "ecf1d69406a8445d"
        assert module.CONTRACT_REVISION == 3
        assert module.BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION == "1.3"
        assert module.BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION == "1.0"
        assert module.BOT_PERSONAL_MEMORY_DOMAIN == "bot_self_schedule"
        assert len(module.BOT_PERSONAL_MEMORY_TYPES) == 12
        assert set(module.TYPE_CONTRACTS) == set(module.BOT_PERSONAL_MEMORY_TYPES)
        assert module.contract_self_check() == []
        assert module.compute_contract_fingerprint() == module.CONTRACT_FINGERPRINT


def test_five_window_boundaries_and_midnight_wrap():
    expected = {
        0: "late_night",
        359: "late_night",
        360: "morning",
        659: "morning",
        660: "noon",
        869: "noon",
        870: "afternoon",
        1079: "afternoon",
        1080: "evening",
        1259: "evening",
        1260: "late_night",
        1439: "late_night",
    }
    for minutes, slug in expected.items():
        assert contract.window_for_minutes(minutes) == slug
    assert contract.window_for_minutes(-1) == "late_night"
    assert contract.window_for_minutes(24 * 60) == "late_night"


def test_normalize_window_and_legacy_migration_never_guesses_without_timestamp():
    assert contract.normalize_window(" MORNING ") == "morning"
    assert contract.normalize_window("凌晨") == "late_night"
    assert contract.normalize_window("unknown") == ""
    assert contract.migrate_legacy_window("late_night") == "late_night"
    assert contract.migrate_legacy_window("morning") == ""
    assert contract.migrate_legacy_window("afternoon") == ""
    assert contract.migrate_legacy_window("evening") == ""
    assert contract.migrate_legacy_window("morning", 11 * 60) == "noon"


def test_descriptor_fields_and_cross_side_descriptor_equality():
    companion = contract.capability_descriptor()
    memory = memory_contract.capability_descriptor()
    assert companion == memory
    assert companion["contract_fingerprint"] == "ecf1d69406a8445d"
    assert companion["contract_revision"] == 3
    assert companion["capability_schema_version"] == "1.3"
    assert companion["canonical_schema_version"] == 3
    assert companion["legacy_canonical_schema_versions"] == [1, 2]
    assert companion["payload_schema_version"] == "1.0"
    assert companion["memory_domain"] == "bot_self_schedule"
    assert companion["windows"] == list(contract.WINDOW_SLUGS)
    assert len(companion["memory_types"]) == 12
