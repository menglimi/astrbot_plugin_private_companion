"""Build one self-contained V2 plugin archive from source only."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CORE_ID = "astrbot_plugin_private_companion"
VERSION = "7.0.13"
RELEASE_SCHEMA = "companion.v2.release.v2"
NATIVE_EXTENSION_FILES = {
    "content": ("companion_v2/content/assembly.py", "companion_v2/content/provider.py",
                "astrbot_host/native_extensions/content.py"),
    "image": ("astrbot_host/native_extensions/image_native/bootstrap.py",
              "astrbot_host/native_extensions/image_native/owner.py",
              "astrbot_host/native_extensions/image_native/extension.py",
              "astrbot_host/native_extensions/image_native/adapter.py",
              "astrbot_host/native_extensions/image_settings.py"),
    "screen": ("astrbot_host/native_extensions/screen_provider.py",
               "astrbot_host/native_extensions/screens.py"),
    "reality": ("companion_v2/reality/provider.py",
                "astrbot_host/native_extensions/reality/runtime.py",
                "astrbot_host/native_extensions/reality/_mihome_login_worker.py",
                "astrbot_host/native_extensions/reality/_mihome_spec_worker.py",
                "astrbot_host/native_extensions/reality/mihome_arc4_compat.py",
                "astrbot_host/native_extensions/reality/settings-schema.json",
                "astrbot_host/native_extensions/reality/settings-defaults.json",
                "astrbot_host/native_extensions/reality/assets/reality_touch_device_test.mp3"),
    "together": ("companion_v2/together/provider.py",
                 "astrbot_host/native_extensions/together/runtime.py",
                 "astrbot_host/native_extensions/together/settings-schema.json",
                 "astrbot_host/native_extensions/together/settings-defaults.json",
                 "astrbot_host/native_extensions/together/web/index.html",
                 "astrbot_host/native_extensions/together/web/app.js",
                 "astrbot_host/native_extensions/together/web/app.css",
                 "astrbot_host/native_extensions/together/web/default-avatar.svg",
                 "astrbot_host/native_extensions/together/web/lucide.min.js"),
    "game": ("companion_v2/games/native/provider.py", "companion_v2/games/native/xiangqi.py",
             "astrbot_host/native_extensions/game/runtime.py",
             "astrbot_host/native_extensions/game/settings-schema.json",
             "astrbot_host/native_extensions/game/settings-defaults.json",
             "astrbot_host/native_extensions/game/web/index.html",
             "astrbot_host/native_extensions/game/web/app.js",
             "astrbot_host/native_extensions/game/web/app.css",
             "astrbot_host/native_extensions/game/web/native.html",
             "astrbot_host/native_extensions/game/web/native.js",
             "astrbot_host/native_extensions/game/web/native-gomoku.js",
             "astrbot_host/native_extensions/game/web/native-gomoku-input.js",
             "astrbot_host/native_extensions/game/web/native.css",
             "astrbot_host/native_extensions/game/web/lucide.min.js"),
    "live": ("companion_v2/live/provider.py",
             "astrbot_host/native_extensions/live/runtime.py",
             "astrbot_host/native_extensions/live/soullink_bridge.mjs",
             "astrbot_host/native_extensions/live/settings-schema.json",
             "astrbot_host/native_extensions/live/settings-defaults.json",
             "astrbot_host/native_extensions/live/blivedm/__init__.py",
             "astrbot_host/native_extensions/live/blivedm/LICENSE",
             "astrbot_host/native_extensions/live/vendor/soullink_emotion_engine/index.mjs",
             "astrbot_host/native_extensions/live/vendor/soullink_emotion_engine/chunk-WF7NFOHV.mjs",
             "astrbot_host/native_extensions/live/vendor/soullink_emotion_engine/LICENSE"),
}
NATIVE_ASSEMBLY_FILES = ("astrbot_host/native_extensions/__init__.py",
                         "astrbot_host/native_extensions/builtins.py",
                         "astrbot_host/native_extensions/settings.py",
                         "astrbot_host/native_extensions/service.py")
NATIVE_CAPABILITY_BASELINE = "tools/native_capability_baseline.json"
NATIVE_HASH_GLOBS = {
    "content": "companion_v2/content",
    "image": "astrbot_host/native_extensions/image_native",
    "reality": "companion_v2/reality",
    "together": "companion_v2/together",
}
NATIVE_HASH_FILES = {
    "screen": ("astrbot_host/native_extensions/screen_provider.py",),
    "game": (
        "companion_v2/games/native/provider.py", "companion_v2/games/tictactoe.py",
        "companion_v2/games/gomoku.py", "companion_v2/games/pig_dice.py",
        "companion_v2/games/blackjack.py", "companion_v2/games/xiangqi.py",
        "companion_v2/games/pikafish.py", "companion_v2/games/native/xiangqi.py",
        "companion_v2/games/turtle_soup.py", "companion_v2/games/draw_guess.py",
        "companion_v2/games/native/context.py",
    ),
    "live": ("companion_v2/live/provider.py",),
}
LEGACY_PROVIDER_IDS = frozenset("astrbot_plugin_" + name + "_companion" for name in
    ("content", "image", "reality", "screen", "together", "game", "live_stream"))
CORE_DIRS = ("companion_v2", "astrbot_host", "pages", "docs", "tests", "fixtures", "tools")
ROOT_FILES = {
    "metadata.yaml", "_conf_schema.json", "README.md", "CHANGELOG.md", "CONTRIBUTING.md",
    "requirements.txt", "requirements-optional-input.txt", "requirements-optional-mic.txt",
    "pyproject.toml", "LICENSE", "NOTICE", "logo.png", ".editorconfig",
}
EXCLUDED_DIRS = {
    "data", ".git", "__pycache__", ".pytest_cache", "node_modules", "logs",
    "dist", "build", "_archive", "protected_model", "desktop_pet", ".codex-plugin", "%SystemDrive%",
    # Design exports and expression-page design boards are development references,
    # not runtime resources. RC22 shipped 13 of them (about 5.4 MB) by mistake.
    "page-designs", "design-previews", "design-exports", "expression-redesign-20261007",
}
EXCLUDED_FILES = {
    # 生成物与开发快照：不是运行时资源。pages/ 下的这些文件此前有 14.06 MB
    # （压缩后 10.19 MB，占整包四分之一）被原样打进 ZIP——单个 theme-preview.html
    # 就占 25.2%。它们由 tools/build_*.py 按需生成，落点在 build/。
    # 保留条目而不是直接依赖文件不存在：重新生成一次也不会把包撑大。
    "pages/陪伴小屋/shell-preview.html",
    "pages/陪伴小屋/shell-preview-mock.html",
    "pages/陪伴小屋/theme-preview.html",
    "pages/陪伴小屋/v2-surface-preview.html",
    "pages/陪伴小屋/_probe-timeline.html",
    # 文档侧的设计稿快照同样是生成物（contact-rhythm 例外：它是生成器读的模板）。
    "docs/V2_SURFACE_PREVIEW_20260928.html",
    "docs/RELEASE_V2_0.md",
    "docs/INTEGRATION_ACCEPTANCE_V2_0.md",
    # The migration acceptance note contains counts from a real private data set.
    "docs/V670_TO_V700_MIGRATION_20261019.md",
    "fixtures/reply_efficiency_preview.json",
    # This standalone mini-game is not part of the companion plugin release.
    "tests/bubble_bonbon_smoke.mjs",
    "tests/_trace_apply.py",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".out", ".db", ".sqlite", ".sqlite3", ".bak", ".backup"}
TEXT_SUFFIXES = {
    ".py", ".js", ".cjs", ".mjs", ".css", ".html", ".md", ".txt", ".json",
    ".yaml", ".yml", ".toml", ".sql", ".svg", ".ps1", ".sh", ".bat", ".ini", ".conf", ".csv",
}
SECRET_PATTERNS = [
    re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9_]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9])abk_[A-Za-z0-9_-]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*[A-Za-z0-9+/=\r\n]{80,}-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def native_artifact_source_files(root):
    names = {name for group in NATIVE_HASH_FILES.values() for name in group}
    for directory in NATIVE_HASH_GLOBS.values():
        names.update(path.relative_to(root).as_posix()
                     for path in (root / directory).glob("*.py"))
    return {name: checked_payload(root / name, root) for name in sorted(names)}


def native_artifact_hashes(files):
    """Mirror each built-in provider's runtime artifact_hash calculation."""
    result = {}
    for capability, directory in NATIVE_HASH_GLOBS.items():
        names = sorted(name for name in files
                       if Path(name).parent.as_posix() == directory and name.endswith(".py"))
        if not names:
            raise ValueError("native capability source files missing: " + capability)
        value = hashlib.sha256()
        for name in names:
            value.update(Path(name).name.encode("utf-8") + b"\x00" + files[name] + b"\x00")
        result[capability] = value.hexdigest()
    for capability, names in NATIVE_HASH_FILES.items():
        missing = sorted(set(names).difference(files))
        if missing:
            raise ValueError("native capability source files missing: " + ", ".join(missing))
        if capability == "game":
            result[capability] = digest(b"".join(files[name] for name in names))
        else:
            result[capability] = digest(files[names[0]])
    return dict(sorted(result.items()))


def native_baseline_document(version, hashes):
    return {"schema": "companion.native-capability-baseline.v1",
            "package_version": version, "artifact_hashes": dict(sorted(hashes.items()))}


def verify_native_baseline(version, hashes, baseline):
    if (type(baseline) is not dict
            or set(baseline) != {"schema", "package_version", "artifact_hashes"}
            or baseline["schema"] != "companion.native-capability-baseline.v1"
            or type(baseline["package_version"]) is not str
            or type(baseline["artifact_hashes"]) is not dict):
        raise ValueError("native capability baseline is invalid")
    if baseline["package_version"] != version:
        raise ValueError("native capability baseline version is stale; after bumping the release version, run "
                         "tools/build_v2_release.py --refresh-native-capability-baseline")
    if baseline["artifact_hashes"] != hashes:
        changed = sorted(set(baseline["artifact_hashes"]) | set(hashes))
        changed = [name for name in changed if baseline["artifact_hashes"].get(name) != hashes.get(name)]
        raise ValueError("native capability artifact changed without a package version bump: "
                         + ", ".join(changed) + "; bump the release version, then refresh the native baseline")


def release_version_key(version):
    """Order the repository's letter hotfixes after their numeric release."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)([a-z]?)", version)
    if not match:
        raise ValueError("native capability baseline requires major.minor.patch with an optional letter revision")
    return (*map(int, match.groups()[:3]), match.group(4))


def refresh_native_baseline(root, version):
    path = root / NATIVE_CAPABILITY_BASELINE
    current_hashes = native_artifact_hashes(native_artifact_source_files(root))
    baseline = None
    if path.exists():
        baseline = json.loads(path.read_text(encoding="utf-8"))
    if baseline is not None:
        if (type(baseline) is not dict or baseline.get("schema") != "companion.native-capability-baseline.v1"
                or type(baseline.get("package_version")) is not str
                or type(baseline.get("artifact_hashes")) is not dict):
            raise ValueError("native capability baseline is invalid")
        previous_version = baseline["package_version"]
        previous_key = release_version_key(previous_version)
        current_key = release_version_key(version)
        changed = baseline["artifact_hashes"] != current_hashes
        if current_key < previous_key or (changed and current_key <= previous_key):
            raise ValueError("native capability hashes changed; the release version must advance before refreshing")
    document = native_baseline_document(version, current_hashes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return document


def allowed(path, root):
    relative = path.relative_to(root)
    return (relative.as_posix() not in EXCLUDED_FILES
            and relative.as_posix() != "fixtures/proactive/news_feed_juya_20261004.xml"
            and not any(part in EXCLUDED_DIRS for part in relative.parts)
            and not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve())
            and path.suffix.lower() not in EXCLUDED_SUFFIXES
            and not path.name.startswith(("_tmp", ".env", "REGISTRATION_COORDINATION_")))


def sources(root, directories):
    # Do not walk data roots and then filter their files: never enter them.
    selected = [p for p in root.iterdir() if p.is_file() and (p.name in ROOT_FILES or p.suffix == ".py")]
    for name in directories:
        folder = root / name
        if not folder.is_dir():
            continue
        for base, children, filenames in os.walk(folder, followlinks=False):
            children[:] = sorted(child for child in children if allowed(Path(base) / child, root))
            selected.extend(Path(base) / name for name in sorted(filenames))
    return sorted({p for p in selected if allowed(p, root)}, key=lambda p: p.relative_to(root).as_posix())


def checked_payload(path, root):
    payload = path.read_bytes()
    if any(pattern.search(payload.replace(b"\\n", b"\n")) for pattern in SECRET_PATTERNS):
        raise ValueError("credential-like content rejected in " + str(path.relative_to(root)))
    # The historical V1 classification source keeps an XLSX container under a
    # .csv name. It is a binary, read-only source documented by the project;
    # normalize real CSV text while preserving that workbook byte-for-byte.
    is_historical_workbook = path.suffix.lower() == ".csv" and payload.startswith(b"PK\x03\x04")
    if (path.suffix.lower() in TEXT_SUFFIXES and not is_historical_workbook) or path.name in {"LICENSE", "NOTICE", ".editorconfig"}:
        text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if path.suffix == ".py":
            ast.parse(text, filename=str(path.relative_to(root)))
        if path.suffix == ".json":
            json.loads(text)
        payload = (text.rstrip("\n") + "\n").encode("utf-8")
    return payload


def zip_entries(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 25, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("archive CRC failed: " + path.name)


def metadata(root):
    raw = (root / "metadata.yaml").read_text(encoding="utf-8-sig")
    def field(name):
        found = re.search(r"^" + name + r":\s*(.+)$", raw, re.MULTILINE)
        if not found:
            raise ValueError("metadata missing " + name + ": " + root.name)
        return found.group(1).strip().strip("\"'")
    return {"plugin_id": field("name"), "version": field("version"),
            "display_name": field("display_name"), "astrbot_version": field("astrbot_version")}


def verify_runtime_sources(files):
    """Check the package's sole AstrBot registration and internal module inventory."""
    root_python = sorted(name for name in files if "/" not in name and name.endswith(".py"))
    if root_python != ["main.py"]:
        raise ValueError("the only root Python source must be main.py")
    required = set(NATIVE_ASSEMBLY_FILES)
    required.update(name for names in NATIVE_EXTENSION_FILES.values() for name in names)
    missing = sorted(required.difference(files))
    if missing:
        raise ValueError("bundled native extension files missing: " + ", ".join(missing))
    stars, registrations = [], []
    plugin_name = product_version = None
    for name, payload in files.items():
        if not name.endswith(".py") or not (name == "main.py" or name.startswith(("companion_v2/", "astrbot_host/"))):
            continue
        tree = ast.parse(payload.decode("utf-8"), filename=name)
        star_names, register_names = {"Star"}, {"register"}
        for node in ast.walk(tree):
            imports = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [(node.module or "") + "." + alias.name for alias in node.names]
                       if isinstance(node, ast.ImportFrom) else [])
            if any(LEGACY_PROVIDER_IDS.intersection(module.split(".")) for module in imports):
                raise ValueError("runtime imports a separate companion plugin: " + name)
            if isinstance(node, ast.ImportFrom) and node.module == "astrbot.api.star":
                star_names.update(alias.asname or alias.name for alias in node.names if alias.name == "Star")
                register_names.update(alias.asname or alias.name for alias in node.names if alias.name == "register")
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                method = (node.func.id if isinstance(node.func, ast.Name)
                          else node.func.attr if isinstance(node.func, ast.Attribute) else "")
                module = node.args[0].value
                if method in {"import_module", "__import__"} and isinstance(module, str) and LEGACY_PROVIDER_IDS.intersection(module.split(".")):
                    raise ValueError("runtime dynamically imports a separate companion plugin: " + name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if any((isinstance(base, ast.Name) and base.id in star_names)
                       or (isinstance(base, ast.Attribute) and base.attr == "Star") for base in node.bases):
                    stars.append((name, node.name))
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id in register_names:
                        registrations.append((name, node.name, decorator))
        if name == "main.py":
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PLUGIN_NAME" for target in node.targets):
                    if isinstance(node.value, ast.Constant):
                        plugin_name = node.value.value
        if name == "companion_v2/product/__init__.py":
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PRODUCT_VERSION" for target in node.targets):
                    if isinstance(node.value, ast.Constant):
                        product_version = node.value.value
    if len(stars) != 1 or stars[0][0] != "main.py":
        raise ValueError("V2 must contain exactly one AstrBot Star in main.py")
    if len(registrations) != 1 or registrations[0][:2] != stars[0]:
        raise ValueError("V2 must register only its single main.py Star")
    registration = registrations[0][2]
    if not registration.args:
        raise ValueError("AstrBot registration identity missing")
    identity = registration.args[0]
    registration_name = (identity.value if isinstance(identity, ast.Constant)
                         else plugin_name if isinstance(identity, ast.Name) and identity.id == "PLUGIN_NAME" else None)
    if plugin_name != CORE_ID or registration_name != CORE_ID:
        raise ValueError("PLUGIN_NAME and AstrBot registration must use the V2 identity")
    if product_version != VERSION:
        raise ValueError("PRODUCT_VERSION must match the release version")
    return {"registered_stars": 1, "native_extensions": sorted(NATIVE_EXTENSION_FILES),
            "external_plugin_dependencies": []}


def build_plugin(root, directories, output):
    info = metadata(root)
    if info["plugin_id"] != CORE_ID:
        raise ValueError("metadata identity mismatch: " + info["plugin_id"])
    if info["version"] != VERSION:
        raise ValueError("V2 version mismatch")
    relative_files = {}
    for path in sources(root, directories):
        relative_files[path.relative_to(root).as_posix()] = checked_payload(path, root)
    required = {"main.py", "metadata.yaml", "_conf_schema.json", "CHANGELOG.md", "pyproject.toml",
                "companion_v2/legacy/configuration_rules.json",
                "pages/陪伴小屋/index.html", "pages/陪伴小屋/page-build.json",
                NATIVE_CAPABILITY_BASELINE}
    if not required.issubset(relative_files):
        raise ValueError("incomplete plugin source: " + root.name)
    project_version = re.search(r'^version\s*=\s*"([^"]+)"$', relative_files["pyproject.toml"].decode("utf-8"), re.MULTILINE)
    if not project_version or project_version.group(1) != VERSION:
        raise ValueError("pyproject version must match the release version")
    runtime_contract = verify_runtime_sources(relative_files)
    verify_native_baseline(VERSION, native_artifact_hashes(relative_files),
                           json.loads(relative_files[NATIVE_CAPABILITY_BASELINE]))
    public_entries = [name for name in relative_files if len(Path(name).parts) == 3
                      and Path(name).parts[0] == "pages" and name.endswith("/index.html")]
    if public_entries != ["pages/陪伴小屋/index.html"]:
        raise ValueError("V2 must expose exactly one Dashboard page: 陪伴小屋")
    manifest = json.loads(relative_files["pages/陪伴小屋/page-build.json"])
    for name, expected in manifest["files"].items():
        source = "pages/陪伴小屋/" + name
        if source not in relative_files or digest(relative_files[source]) != expected:
            raise ValueError("stale cottage asset: " + name + "; run sync_framework_page.py first")
    files = {CORE_ID + "/" + name: payload for name, payload in relative_files.items()}
    file_manifest = {name: digest(payload) for name, payload in relative_files.items()}
    files[CORE_ID + "/RELEASE_FILES.json"] = (json.dumps({"product_version": VERSION,
        "plugin": info, **runtime_contract, "files": file_manifest}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    filename = CORE_ID + "-" + info["version"] + ".zip"
    target = output / filename
    zip_entries(target, files)
    return {**info, "filename": filename, "sha256": digest(target.read_bytes()),
            "bytes": target.stat().st_size, "files": len(files),
            "integration": "native-bundled", "execution_owner": CORE_ID, **runtime_contract}


def build(output, core_only=False):
    # Keep the old flag/API as a compatibility no-op; every build is now complete.
    output = output.resolve()
    if output.is_relative_to(ROOT.parent):
        raise ValueError("release output must be outside installed plugin directories")
    output.mkdir(parents=True, exist_ok=True)
    # Stage the complete release first. A validation failure publishes no new ZIP.
    with TemporaryDirectory(prefix="companion-v2-package-") as temporary:
        stage = Path(temporary)
        packages = [build_plugin(ROOT, CORE_DIRS, stage)]
        manifest = {"schema": RELEASE_SCHEMA, "product": "我会永远陪着你", "version": VERSION,
                    "astrbot_version": packages[0]["astrbot_version"],
                    "data_policy": "隔离运行数据；仅在用户确认后读取旧版来源并写入新数据根",
                    "provider_integration": "Seven native internal extensions are bundled and owned by one plugin",
                    "native_extensions": sorted(NATIVE_EXTENSION_FILES),
                    "external_plugin_dependencies": [],
                    "packages": packages}
        (stage / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        hashes = [(p.name, digest(p.read_bytes())) for p in sorted(stage.iterdir()) if p.is_file()]
        (stage / "SHA256SUMS.txt").write_text("".join(sha + "  " + name + "\n" for name, sha in hashes), encoding="utf-8", newline="\n")
        for path in stage.iterdir():
            target = output / path.name
            target.write_bytes(path.read_bytes())
        report = {"output": str(output), "packages": len(packages),
                  "archives": [{"name": p.name, "sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
                               for p in sorted(stage.glob("*.zip"))]}
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--core-only", action="store_true", help="deprecated compatibility flag; all seven internal extensions are always bundled")
    parser.add_argument("--refresh-native-capability-baseline", action="store_true",
                        help="pin current native hashes after advancing the release version")
    args = parser.parse_args()
    if args.refresh_native_capability_baseline:
        if metadata(ROOT)["version"] != VERSION:
            raise ValueError("metadata.yaml version must match the release version before refreshing the baseline")
        source_files = {path.relative_to(ROOT).as_posix(): checked_payload(path, ROOT)
                        for path in sources(ROOT, CORE_DIRS)}
        verify_runtime_sources(source_files)
        project_version = re.search(r'^version\s*=\s*"([^"]+)"$',
            source_files["pyproject.toml"].decode("utf-8"), re.MULTILINE)
        if not project_version or project_version.group(1) != VERSION:
            raise ValueError("pyproject version must match the release version before refreshing the baseline")
        print(json.dumps(refresh_native_baseline(ROOT, VERSION), ensure_ascii=False, indent=2))
        return
    if args.output is None:
        parser.error("--output is required unless refreshing the native capability baseline")
    print(json.dumps(build(args.output, args.core_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
