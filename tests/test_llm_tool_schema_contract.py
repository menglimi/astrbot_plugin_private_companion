from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOL_AUTHORING_HASHES = {
    "pc_find_reaction_image": "c5dbffd13b3fd45d830ce733e98f9b2861ff8167f47cabfa4f8d93f6a4bd2ed3",
    "pc_generate_photo": "71ebfd8933bf131b64b719b4f107f02c8fb2bff07ff15a1d7e0271f879717c2d",
    "pc_get_group_id_by_name": "273fbcac04d127b464d3ba7467f19d32d560d4952e4814ce1b0dc7e88f189a04",
    "pc_get_specified_group_members": "a731b03b14bce71c6e88aea5d2512331985ec10434a0c30a35d6ec0b129cddf4",
    "pc_get_user_id_by_name": "4b04eb366e909502c644d4ea017defb68ce0cbb0f86b4a9ca681b808e73559f5",
    "pc_manage_memo": "d0d181252504677416ae93aeae226085cc1d0723e73a864367cf9f67f1b2ba53",
    "pc_manage_schedule": "5d2fedded654e99be3f1959df9071dc87c5e08b1fe0f4f0bb4c228bffdbdbc71",
    "pc_query_interaction": "bef2bcb33ebb98ca0ac86c196267fadc006f5e1c798b9a475b42dec2ec6157aa",
    "pc_query_relation_person": "ebd19c1a071da8cc3efb3a7022d2c9cdefac9144fe9b95d9e6de0c205b2e344c",
    "pc_query_wardrobe_detail": "1701eace01a519ba90f6eb23f8d6dbce542e24b10cebb8f4450b4fb6f404dfca",
    "pc_qzone_publish_feed": "de25cc569fa2ba778066738920320387f4e5d08e3e176b642fe312dbe6bd8987",
    "pc_set_outfit_intent": "7faaac90a7628e0574d22cd66aa0274162400bed6594c93ca96e0fdea7a768b3",
    "pc_qzone_reply_my_comment": "ffba39788f1c7cad41b9d4b4c62d035c9ce73775ed08e0e782b5683240779682",
    "pc_qzone_view_feed": "d8a022d25585064e61764ac8ddcb23560e6e621c81e4980eab68eaf46ae1cb9d",
    "pc_relay_message": "3802dd8c78eb2f87cad27b4afbe91fcd0364f1387b3244b8b1632d3d487208cb",
    "pc_schedule_group_relay": "ce8b49800c849ffc8814ed2f7c8e029bd6694d2d2adc517397ee5dad906d0853",
    "pc_send_current_media": "5688ad481e0fcd0da8ee605dc0e0bdacd8c680dbfe08bc6f65e5cfa12b5aef75",
    "pc_send_to_group": "a06428b3f5bca2ad5e3cc496a4a7a4f00570ac53f2791f14549fea7c641258a4",
    "pc_send_to_groups": "b895558fcc2e652f69583d5f4397a187dc86e523c6333d187595e7eded43b1bb",
    "pc_send_to_private_user": "d5d1a84899130c4d4c52046c2a1e9991c71c0c0f872fc8dc89f97144495fca23",
    "pc_send_to_private_users": "19ed26c090a9258d9de1a3a71d715d0fbcfa3cde63aec62b5598c92724954e4a",
    "pc_view_creative_work": "9c7aad21b534ac75e4824ce7e6dfaaf49f8d3f5e9b4c1a6bd9f0b8fe55a20cd8",
}


def _tool_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    for decorator in node.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "llm_tool"
        ):
            continue
        for keyword in decorator.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
    return ""


def _tool_authoring_hashes() -> dict[str, str]:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tool_name = _tool_name(node)
        if not tool_name:
            continue
        payload = json.dumps(
            {
                "function": node.name,
                "signature": ast.dump(
                    node.args,
                    annotate_fields=True,
                    include_attributes=False,
                ),
                "docstring": ast.get_docstring(node, clean=False) or "",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result[tool_name] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return result


def test_llm_tool_schema_authoring_inputs_remain_unchanged() -> None:
    """Prompt refactors must not alter AstrBot's FunctionTool schema inputs."""

    assert _tool_authoring_hashes() == EXPECTED_TOOL_AUTHORING_HASHES
