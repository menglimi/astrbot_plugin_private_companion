# -*- coding: utf-8 -*-
"""在可终止子进程中获取公开设备规格并修复缺失的本地化表。"""

import asyncio
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _prefer_astrbot_runtime_packages() -> None:
    """Keep vendored plugin dependencies from shadowing AstrBot's runtime."""
    candidates = [
        Path(sys.executable).resolve().parent / "Lib" / "site-packages",
    ]
    try:
        import sysconfig

        for key in ("purelib", "platlib"):
            configured = sysconfig.get_paths().get(key)
            if configured:
                candidates.append(Path(configured))
    except Exception:
        pass

    for candidate in reversed(candidates):
        if not candidate.is_dir():
            continue
        normalized = os.path.normcase(str(candidate))
        sys.path[:] = [
            entry
            for entry in sys.path
            if os.path.normcase(str(entry or "")) != normalized
        ]
        sys.path.insert(0, str(candidate))


def _load_arc4_compat():
    """Load the shared fallback without adding the plugin tree to sys.path."""

    module_path = Path(__file__).resolve().with_name("mihome_arc4_compat.py")
    spec = importlib.util.spec_from_file_location(
        "_companion_mihome_arc4_compat",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("ARC4 compatibility module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_prefer_astrbot_runtime_packages()
try:
    _ARC4_FORCE_FALLBACK = os.environ.get("ASTRBOT_COMPANION_FORCE_ARC4_FALLBACK") == "1"
    _ARC4_NATIVE_ERROR = _load_arc4_compat().ensure_arc4_compat(force_fallback=_ARC4_FORCE_FALLBACK)
except Exception as exc:
    print(
        f"ERROR: 内置 ARC4 兼容层初始化失败 ({type(exc).__name__})",
        flush=True,
    )
    sys.exit(1)
if _ARC4_NATIVE_ERROR:
    print(
        f"[WORKER] 原生 ARC4 不可用（{_ARC4_NATIVE_ERROR}），已启用内置兼容实现。",
        flush=True,
    )

import httpx
from mijiaAPI import __version__ as mijia_version
from mijiaAPI import get_device_info
from mijiaAPI import devices as mijia_devices


MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
SPEC_URL_TEMPLATE = "https://home.miot-spec.com/spec/{model}"
APP_JSON_PATTERN = re.compile(
    r'<script data-page="app" type="application/json">(.*?)</script>',
    re.DOTALL,
)
SPEC_FETCH_TIMEOUT = 20.0


async def _fetch_spec_page(model: str) -> str:
    """使用异步客户端拉取规格页，避免在 AstrBot 主进程阻塞网络。"""

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": f"mijiaAPI/{mijia_version}"},
        timeout=SPEC_FETCH_TIMEOUT,
    ) as client:
        response = await client.get(SPEC_URL_TEMPLATE.format(model=model))
        if response.status_code != 200:
            raise ValueError(f"设备规格页面返回 {response.status_code}")
        return response.text


def _patch_i18n_fallback(page_text: str, model: str) -> str:
    """补齐 ``zh_cn`` 表，同时保留已有中文并用英文补全缺项。"""

    match = APP_JSON_PATTERN.search(page_text)
    if match is None:
        raise ValueError("设备规格页面缺少应用数据")
    content = json.loads(match.group(1))
    if not isinstance(content, dict):
        raise ValueError("设备规格结构异常")

    props = content.get("props")
    if not isinstance(props, dict):
        raise ValueError("设备规格结构异常")
    product = props.get("product")
    if not isinstance(product, dict):
        raise ValueError("设备规格结构异常")
    returned_model = str(product.get("model") or "").strip()
    if returned_model != model:
        raise ValueError("设备规格型号不匹配")

    i18n = props.get("i18n")
    if not isinstance(i18n, dict):
        i18n = {}
        props["i18n"] = i18n
    english = i18n.get("en")
    chinese = i18n.get("zh_cn")
    merged: dict[str, Any] = {}
    if isinstance(english, dict):
        merged.update(english)
    if isinstance(chinese, dict):
        merged.update(chinese)
    i18n["zh_cn"] = merged

    patched_json = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return page_text[: match.start(1)] + patched_json + page_text[match.end(1) :]


def _parse_with_pinned_upstream(
    model: str,
    cache_dir: Path,
    page_text: str,
) -> dict:
    """让锁定的 mijiaAPI 解析修补后的页面，避免复制其 schema 逻辑。"""

    patched_page = _patch_i18n_fallback(page_text, model)
    response = SimpleNamespace(status_code=200, text=patched_page)
    original_get = mijia_devices.requests.get

    def _local_get(url: str, **_kwargs: Any) -> SimpleNamespace:
        if url != SPEC_URL_TEMPLATE.format(model=model):
            raise ValueError("设备规格请求地址异常")
        return response

    mijia_devices.requests.get = _local_get
    try:
        return get_device_info(model, cache_path=cache_dir)
    finally:
        mijia_devices.requests.get = original_get


def get_device_info_compat(model: str, cache_dir: Path) -> dict:
    """获取页面并以最小兼容补丁交给锁定版本的 mijiaAPI 解析。"""

    page_text = asyncio.run(_fetch_spec_page(model))
    return _parse_with_pinned_upstream(model, cache_dir, page_text)


def main() -> None:
    if len(sys.argv) != 3:
        print("ERROR: 参数数量错误", flush=True)
        raise SystemExit(2)

    model = str(sys.argv[1] or "").strip()
    cache_dir = Path(sys.argv[2])
    if not MODEL_PATTERN.fullmatch(model):
        print("ERROR: 设备型号格式异常", flush=True)
        raise SystemExit(2)
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        print("ERROR: 临时缓存目录类型异常", flush=True)
        raise SystemExit(2)

    try:
        spec = get_device_info_compat(model, cache_dir)
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("properties"), list)
            or not isinstance(spec.get("actions"), list)
        ):
            raise ValueError("设备规格结构异常")

        cache_path = cache_dir / f"{model}.json"
        if cache_path.is_symlink() or not cache_path.is_file():
            raise ValueError("设备规格缓存文件类型异常")
        with cache_path.open("r", encoding="utf-8") as file:
            persisted = json.load(file)
        if persisted != spec:
            raise ValueError("设备规格缓存校验失败")
        cache_path.chmod(0o600)
    except Exception as exc:
        print(
            f"ERROR: 设备规格获取失败 ({type(exc).__name__})",
            flush=True,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
