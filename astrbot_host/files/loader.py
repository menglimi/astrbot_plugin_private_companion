"""Download only the frozen native attachment, then extract text in a cancellable process."""

import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

from companion_v2.adapters.astrbot.files import NativeFileLocator, FileText, TEXT_SUFFIXES, DOCUMENT_SUFFIXES
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind
from ..media import fetch_attachment, read_host_resolved_attachment


async def decode_document(content, locator, *, maximum_chars, formats):
    process = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8", "-m", "astrbot_host.files.worker",
        locator.name, str(maximum_chars), formats, cwd=str(Path(__file__).resolve().parents[2]),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        output, _ = await process.communicate(content)
        if len(output) > maximum_chars * 6 + 8192:
            raise TaskError("host_file_limit", "document text exceeds its extraction budget")
        data = json.loads(output)
        if process.returncode:
            code = data.get("error", "host_file_invalid")
            raise TaskError(code if code.startswith("host_file_") else "host_file_invalid", "document extraction failed")
        return FileText(**{**data, "position": locator.position})
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()


async def load_native_file(locator, *, client, scope, maximum_bytes, maximum_chars, formats):
    if type(locator) is not NativeFileLocator:
        raise TaskError("host_file_location_invalid", "file intake requires a frozen native attachment")
    suffix = Path(locator.name).suffix.lower()
    if suffix not in TEXT_SUFFIXES | (DOCUMENT_SUFFIXES if formats == "text_and_documents" else frozenset()):
        raise TaskError("host_file_format_unsupported", "document format is not selected or supported")
    value = locator.locator
    if not value:
        raise TaskError("host_file_unavailable", "file has no original native attachment locator")
    if value.startswith("base64://"):
        encoded = value[9:]
        if len(encoded) > (maximum_bytes + 2) // 3 * 4:
            raise TaskError("host_file_limit", "file exceeds its original byte budget")
        content = base64.b64decode(encoded, validate=True)
    elif locator.host_resolved is True:
        content = await asyncio.to_thread(
            read_host_resolved_attachment, value, maximum_bytes)
    else:
        if not value.startswith(("https://", "http://")):
            if len(value) > 200 or any(char in value for char in ("/", "\\", ":", "\x00")):
                raise TaskError("host_file_location_invalid", "network attachment cannot grant local disk access")
            route = {"self_id": int(scope.account_id), "file_id": value}
            if scope.kind is ScopeKind.GROUP:
                action = "get_group_file_url"
                route["group_id"] = int(scope.native_scope_id)
            else:
                action = "get_private_file_url"
            response = await client.call_action(action, **route)
            value = response.get("url", "") if isinstance(response, dict) else ""
        content = await fetch_attachment(value, maximum_bytes=maximum_bytes)
    if not 0 < len(content) <= maximum_bytes:
        raise TaskError("host_file_limit", "file exceeds its original byte budget")
    return await decode_document(content, locator, maximum_chars=maximum_chars, formats=formats)
