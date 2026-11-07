"""Bounded image intake. URLs/paths never enter prompts or durable metadata."""

import asyncio
import base64
import hashlib
import io
import ipaddress
from pathlib import Path
from urllib.parse import urljoin
import warnings

import aiohttp
from PIL import Image
from yarl import URL

from companion_v2.adapters.astrbot.native import NativeImageLocator
from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from companion_v2.execution.models import TaskError

from .animation import inspect_animation


# Transparent DNS proxies (Clash/mihomo ``fake-ip`` mode) answer ordinary
# names from the RFC 2544 benchmarking range so that the proxy stack can route
# them.  Such an address never belongs to a local service: the connector hands
# the packet to the proxy, which resolves the original name again.  A name
# resolved there is still a public fetch, so only that resolver answer may use
# the exception.  A literal address in the range stays refused in
# ``public_url``, and every other non-global answer (loopback, private,
# link-local, CGNAT, cloud metadata) keeps its existing denial.
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _resolved_publicly(value):
    """Whether a resolver answer may be connected to for a public attachment."""
    address = ipaddress.ip_address(value)
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global or address in _FAKE_IP_NETWORK


def public_url(value):
    if type(value) is not str or not value or len(value) > 8192 or any(c.isspace() or ord(c) < 32 for c in value):
        raise TaskError("host_image_location_invalid", "image URL is invalid")
    try:
        # Use the connector's own URL normalization, including IDNA hosts.
        parsed = URL(value)
        host = parsed.raw_host
        if (parsed.scheme not in ("http", "https") or not host or parsed.raw_user is not None
                or parsed.raw_password is not None or parsed.fragment or parsed.port not in (None, 80, 443)):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # aiohttp bypasses DNS for numeric/colon hosts. Refuse legacy IPv4
            # spellings that the OS could interpret as an unchecked address.
            if ":" in host or host.replace(".", "").isdigit():
                raise ValueError
            address = None
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError:
        raise TaskError("host_image_location_invalid", "image URL must resolve to a public web attachment") from None
    return str(parsed)


class PublicImageResolver(aiohttp.ThreadedResolver):
    async def resolve(self, host, port=0, family=0):
        values = await super().resolve(host, port, family)
        # Validate the addresses actually handed to the connector. A separate
        # DNS preflight would allow a later lookup to bind to a different host.
        if not values or any(not _resolved_publicly(v["host"]) for v in values):
            raise TaskError("host_image_location_invalid", "image attachment resolved outside the public network")
        return values


async def fetch_image(value):
    return await fetch_attachment(value, maximum_bytes=MAX_IMAGE_BYTES)


async def fetch_attachment(value, *, maximum_bytes):
    current = public_url(value)
    resolver = PublicImageResolver()
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, limit=1)
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                                        timeout=aiohttp.ClientTimeout(total=15), auto_decompress=False) as session:
            for redirect in range(4):
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        if redirect == 3 or "Location" not in response.headers:
                            raise TaskError("host_image_fetch_failed", "image redirect could not be resolved")
                        current = public_url(urljoin(current, response.headers["Location"]))
                        continue
                    if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                        raise TaskError("host_image_fetch_failed", "original image could not be fetched")
                    if response.content_length is not None and response.content_length > maximum_bytes:
                        raise TaskError("host_image_limit", "image exceeds the intake byte budget")
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if len(content) + len(chunk) > maximum_bytes:
                            raise TaskError("host_image_limit", "image exceeds the intake byte budget")
                        content.extend(chunk)
                    return bytes(content)
    finally:
        await resolver.close()
    raise TaskError("host_image_fetch_failed", "original image could not be fetched")


def inspect_image(content, position):
    """Verify originals; animated formats retain a separately declared frame view."""
    if type(content) is not bytes or not 0 < len(content) <= MAX_IMAGE_BYTES:
        raise TaskError("host_image_limit", "image exceeds the intake byte budget")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}.get(image.format)
                width, height = image.size
                if mime is None:
                    raise TaskError("host_image_format_unsupported", "this image encoding is not supported")
                if width * height > MAX_IMAGE_PIXELS or max(width, height) > 8192:
                    raise TaskError("host_image_limit", "image dimensions exceed the intake budget")
                if (image.format == "GIF" or getattr(image, "is_animated", False)
                        or getattr(image, "custom_mimetype", None) == "image/apng"):
                    return inspect_animation(content, position, mime, width, height)
                image.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
        descriptor = ImageDescriptor(position, mime, width, height, len(content), hashlib.sha256(content).hexdigest())
        return CapturedImage(descriptor, content)
    except TaskError:
        raise
    except Exception:
        raise TaskError("host_image_invalid", "image bytes could not be verified") from None


def _read_host_resolved_image(value):
    """Read a local file the trusted host already vouched for.

    Two producers set ``host_resolved``: the quote resolver, and the adapter's
    check that an inbound attachment was rewritten to a file inside AstrBot's
    temp directory. Both are absolute files; the marker is the authority.
    """
    path = Path(value)
    if not path.is_absolute() or value.startswith(("\\\\", "//")) or not path.is_file():
        raise TaskError("host_image_location_invalid", "resolved image cache must be a local absolute file")
    with path.open("rb") as stream:
        content = stream.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise TaskError("host_image_limit", "image exceeds the intake byte budget")
    return content


async def load_native_image(locator):
    if not isinstance(locator, NativeImageLocator):
        raise TaskError("host_image_location_invalid", "image intake needs a frozen host component")
    try:
        value = locator.locator
        if value.startswith("base64://"):
            encoded = value[len("base64://"):]
            if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                raise TaskError("host_image_limit", "image exceeds the intake byte budget")
            content = base64.b64decode(encoded, validate=True)
        elif locator.host_resolved is True and Path(value).is_absolute():
            content = await asyncio.to_thread(_read_host_resolved_image, value)
        else:
            # Do not interpret a network message's file/path field as local disk authority.
            content = await fetch_image(value)
        return await asyncio.to_thread(inspect_image, content, locator.position)
    except TaskError:
        raise
    except Exception:
        raise TaskError("host_image_fetch_failed", "original image could not be prepared") from None


def read_host_resolved_attachment(value, maximum_bytes):
    """Read an attachment the trusted host already wrote to its temp directory.

    Telegram voice/audio is downloaded by AstrBot into ``<data>/temp`` and the
    Telegram media preparer materializes photos/documents/videos there too.
    Only a file inside that directory is trusted; every other network-message
    path keeps the public-URL rule.
    """
    from companion_v2.adapters.astrbot.host_paths import is_host_temp_file

    if not is_host_temp_file(value):
        raise TaskError("host_attachment_location_invalid",
                        "resolved attachment must be a file inside the host temp directory")
    path = Path(value)
    with path.open("rb") as stream:
        content = stream.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise TaskError("host_attachment_limit", "attachment exceeds the intake byte budget")
    return content
