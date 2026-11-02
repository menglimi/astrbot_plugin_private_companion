"""Prepare a read-only SDK page inventory for the furniture browser audit."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace


PROVIDERS = (
    ("content", "content_companion"), ("image", "image_companion"),
    ("together", "together_companion"), ("game", "game_companion"),
    ("screen", "screen_companion"), ("reality", "reality_companion"),
    ("live", "live_stream_companion"),
)


async def inventory(output: Path) -> dict:
    # Import the real SDK only after the caller has selected a temporary root.
    from astrbot.dashboard.services.plugin_page_service import PluginPageService
    from tools.host_page_render import content_prefix, render_html

    plugins = Path(__file__).resolve().parents[2]
    service = PluginPageService(SimpleNamespace(plugin_store_path=str(plugins)))
    output.mkdir(parents=True, exist_ok=True)
    report = {"providers": [], "bridge_path": str(service.bridge_file)}
    for capability, suffix in PROVIDERS:
        name = "astrbot_plugin_" + suffix
        root = plugins / name / "pages"
        plugin = SimpleNamespace(root_dir_name=name, reserved=False)
        record = {"capability": capability, "plugin": name, "pages": [],
                  "source_entries": [str(p.relative_to(root)) for p in sorted(root.rglob("index.html"))]}
        for page in await service.discover_plugin_pages(plugin):
            entry = await service.resolve_plugin_page_file(plugin, page.name, "")
            html = render_html(service, entry.read_text(encoding="utf-8-sig"),
                name, page.name, "index.html", theme="light")
            saved = output / (capability + ".html")
            saved.write_text(html, encoding="utf-8", newline="\n")
            record["pages"].append({"name": page.name, "entry": str(entry),
                "source_root": str(entry.parent), "rewritten_html": str(saved),
                "asset_prefix": content_prefix(service, name, page.name)})
        report["providers"].append(record)
    (output / "inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="v2-furniture-sdk-", ignore_cleanup_errors=True) as root:
        os.environ["ASTRBOT_ROOT"] = root
        report = asyncio.run(inventory(args.output.resolve()))
    print(json.dumps({item["capability"]: [page["name"] for page in item["pages"]]
                      for item in report["providers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
