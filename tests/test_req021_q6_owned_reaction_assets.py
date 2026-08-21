from __future__ import annotations

import ast
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from owned_reaction_asset_catalog import OwnedReactionAssetCatalog  # noqa: E402


def _single_line(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _path_text(value: Any, limit: int = 1000) -> str:
    return _single_line(value, limit)


def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return default


def _load_owned_lookup_impl() -> Any:
    source = (ROOT / "llm_tool_actions.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LlmToolActionsMixin")
    method = next(
        node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_find_owned_reaction_asset"
    )
    namespace: dict[str, Any] = {
        "Any": Any,
        "logger": logging.getLogger("test_req021_q6"),
        "_single_line": _single_line,
        "OwnedReactionAssetCatalog": OwnedReactionAssetCatalog,
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(ROOT / "llm_tool_actions.py"), "exec"), namespace)
    return namespace["_find_owned_reaction_asset"]


OWNED_LOOKUP_IMPL = _load_owned_lookup_impl()


class OwnedReactionAssetCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.asset_dir = self.data_dir / "owned_reaction_assets"
        self.asset_dir.mkdir()
        self.catalog = OwnedReactionAssetCatalog(self.data_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _entry(self, asset_id: str, name: str, *, tags: list[str], meme_only: bool = True) -> dict[str, Any]:
        path = self.asset_dir / name
        path.write_bytes(f"asset:{name}".encode("utf-8"))
        return {
            "id": asset_id,
            "file": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "tags": tags,
            "meme_only": meme_only,
        }

    def test_hash_directory_extension_and_projection_are_fail_closed(self) -> None:
        valid = self._entry("smile-01", "smile.png", tags=["开心", "smile"])
        invalid = dict(valid, id="outside", file="../outside.png")
        projection = self.catalog.public_projection([valid, invalid, {"id": "bad", "file": "x.gif"}])

        self.assertEqual("ok", projection["items"][0]["status"])
        self.assertFalse(projection["items"][1]["valid"])
        self.assertEqual("outside_managed_directory", projection["items"][1]["status"])
        self.assertNotIn("file", json.dumps(projection, ensure_ascii=False))
        self.assertNotIn("sha256", json.dumps(projection, ensure_ascii=False))
        self.assertIsNone(self.catalog.resolve([invalid], "outside"))

    def test_find_is_stable_and_honors_meme_only(self) -> None:
        alpha = self._entry("alpha", "alpha.png", tags=["开心"])
        beta = self._entry("beta", "beta.webp", tags=["开心"], meme_only=False)
        asset, status, confidence = self.catalog.find([beta, alpha], query="开心", meme_only=True)

        self.assertEqual("ok", status)
        self.assertEqual("alpha", asset.asset_id if asset else "")
        self.assertGreater(confidence, 0.0)
        asset, status, _confidence = self.catalog.find([beta], query="开心", meme_only=True)
        self.assertIsNone(asset)
        self.assertEqual("not_found", status)


class _OwnedReactionHost:
    def __init__(self, data_dir: Path, entries: list[dict[str, Any]]) -> None:
        self.data_dir = str(data_dir)
        self.enable_owned_reaction_asset_workbench = True
        self.owned_reaction_assets = entries


class OwnedReactionToolAndPanelContractTests(unittest.TestCase):
    def test_owned_lookup_precedes_library_and_tool_result_hides_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            asset_dir = data_dir / "owned_reaction_assets"
            asset_dir.mkdir()
            asset = asset_dir / "smile.png"
            asset.write_bytes(b"asset")
            host = _OwnedReactionHost(
                data_dir,
                [{
                    "id": "smile-01",
                    "file": "smile.png",
                    "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                    "tags": ["开心"],
                }],
            )
            lookup = OWNED_LOOKUP_IMPL(host, "开心")

        self.assertEqual("owned_reaction_assets", lookup["source"] if lookup else "")
        self.assertEqual("smile-01", lookup["image_id"] if lookup else "")
        source = (ROOT / "llm_tool_actions.py").read_text(encoding="utf-8")
        runtime_source = source[source.index("async def _pc_find_reaction_image_impl"):]
        self.assertLess(
            runtime_source.index("owned_lookup_finder = getattr"),
            runtime_source.index("library = self._reaction_asset_library()"),
        )
        self.assertIn('"owned_reaction_assets"', source)
        self.assertIn("or internal_attachment", source)

    def test_panel_is_id_only_and_routes_are_read_only(self) -> None:
        api_source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        panel_source = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        routes = {
            (node.elts[0].value, node.elts[2].elts[0].value)
            for node in ast.walk(ast.parse(api_source))
            if isinstance(node, ast.Tuple)
            and len(node.elts) >= 3
            and isinstance(node.elts[0], ast.Constant)
            and isinstance(node.elts[0].value, str)
            and node.elts[0].value.startswith("/reaction_assets/")
            and isinstance(node.elts[2], ast.List)
            and node.elts[2].elts
            and isinstance(node.elts[2].elts[0], ast.Constant)
        }
        self.assertEqual(
            {("/reaction_assets/list", "GET"), ("/reaction_assets/image_data", "GET")},
            routes,
        )
        self.assertIn("/reaction_assets/image_data?id=${encodeURIComponent(assetId)}", panel_source)
        self.assertIn("不显示路径、哈希或来源", panel_source)
        self.assertNotIn("reaction_assets/image_data?path=", panel_source)


if __name__ == "__main__":
    unittest.main()
