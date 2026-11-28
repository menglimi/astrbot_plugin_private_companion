"""Bounded image intake. URLs/paths never enter prompts or durable metadata."""

import asyncio
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import io
import ipaddress
import os
from pathlib import Path
import stat
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


async def fetch_image(value, *, maximum_bytes=MAX_IMAGE_BYTES):
    return await fetch_attachment(value, maximum_bytes=maximum_bytes)


class ReadMeter:
    """Body bytes received by downloads in one ``metered`` block, including failed ones."""

    def __init__(self):
        self.bytes = 0


_meter = ContextVar("companion_v2_attachment_meter", default=None)


@contextmanager
def metered():
    """Count what downloads started in this block receive, through any loader in between."""
    meter = ReadMeter()
    token = _meter.set(meter)
    try:
        yield meter
    finally:
        _meter.reset(token)


_trusted_endpoint = ContextVar("companion_v2_trusted_endpoint", default=None)


@contextmanager
def trusted_endpoint(prefix, *, local_files=False):
    """Fetch URLs under ``prefix`` the way the host's own client does.

    For a bot's Telegram file endpoint, including an operator's self-hosted Bot
    API server: AstrBot's proxy setting applies, private addresses are allowed,
    and redirects must stay under the prefix. With ``local_files``, an absolute
    path is read from disk, as a local-mode Bot API server hands files over on a
    shared disk. Byte limits and the meter apply throughout.
    """
    value = (prefix, bool(local_files)) if isinstance(prefix, str) and prefix else None
    token = _trusted_endpoint.set(value)
    try:
        yield
    finally:
        _trusted_endpoint.reset(token)


def _under_prefix(value, prefix):
    """Same origin as ``prefix`` and a path inside it, after URL normalization (so ``..`` cannot escape)."""
    try:
        url, base = URL(value), URL(prefix)
    except (TypeError, ValueError):
        return False
    return (type(value) is str and not any(c.isspace() or ord(c) < 32 for c in value)
            and (url.scheme, url.raw_host, url.port) == (base.scheme, base.raw_host, base.port)
            and url.raw_user is None and url.raw_password is None and not url.fragment
            and url.raw_path.startswith(base.raw_path.rstrip("/") + "/"))


def _read_local_attachment(value, maximum_bytes):
    with Path(value).open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise TaskError("host_image_fetch_failed", "original attachment is not a regular file")
        if info.st_size > maximum_bytes:
            raise TaskError("host_image_limit", "image exceeds the intake byte budget")
        content = stream.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise TaskError("host_image_limit", "image exceeds the intake byte budget")
    return content


async def fetch_attachment(value, *, maximum_bytes):
    prefix, local_files = _trusted_endpoint.get() or (None, False)
    if local_files and type(value) is str and not value.startswith(("//", "\\")) and Path(value).is_absolute():
        try:
            return await asyncio.to_thread(_read_local_attachment, value, maximum_bytes)
        except TaskError:
            raise
        except OSError:
            raise TaskError("host_image_fetch_failed", "original attachment could not be read") from None
    trusted = prefix is not None and _under_prefix(value, prefix)
    current = value if trusted else public_url(value)
    resolver = aiohttp.ThreadedResolver() if trusted else PublicImageResolver()
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, limit=1)
        # A trusted endpoint goes through AstrBot's proxy setting, as AstrBot's own downloads do.
        async with aiohttp.ClientSession(connector=connector, trust_env=trusted,
                                        timeout=aiohttp.ClientTimeout(total=15), auto_decompress=False) as session:
            for redirect in range(4):
                async with session.get(current, allow_redirects=False,
                                       headers={"Accept-Encoding": "identity"}) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        if redirect == 3 or "Location" not in response.headers:
                            raise TaskError("host_image_fetch_failed", "image redirect could not be resolved")
                        target = urljoin(current, response.headers["Location"])
                        if trusted and not _under_prefix(target, prefix):
                            raise TaskError("host_image_fetch_failed", "image redirect left the trusted endpoint")
                        current = target if trusted else public_url(target)
                        continue
                    if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                        raise TaskError("host_image_fetch_failed", "original image could not be fetched")
                    if response.content_length is not None and response.content_length > maximum_bytes:
                        raise TaskError("host_image_limit", "image exceeds the intake byte budget")
                    content = bytearray()
                    meter = _meter.get()
                    async for chunk in response.content.iter_chunked(65536):
                        if meter is not None:
                            meter.bytes += len(chunk)
                        if len(content) + len(chunk) > maximum_bytes:
                            raise TaskError("host_image_limit", "image exceeds the intake byte budget")
                        content.extend(chunk)
                    return bytes(content)
    finally:
        await resolver.close()
    raise TaskError("host_image_fetch_failed", "original image could not be fetched")


def inspect_image(content, position):
    """Verify originals; animated formats retain a separately declared frame view."""
    if type(content) is not bytes or not content:
        raise TaskError("host_image_invalid", "image bytes are empty or invalid")
    if len(content) > MAX_IMAGE_BYTES:
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


def _read_host_resolved_image(value, maximum_bytes=MAX_IMAGE_BYTES):
    """Read a local file the trusted host already vouched for.

    Two producers set ``host_resolved``: the quote resolver, and the adapter's
    check that an inbound attachment was rewritten to a file inside AstrBot's
    temp directory. Both are absolute files; the marker is the authority.
    """
    path = Path(value)
    if not path.is_absolute() or value.startswith(("\\\\", "//")) or not path.is_file():
        raise TaskError("host_image_location_invalid", "resolved image cache must be a local absolute file")
    with path.open("rb") as stream:
        content = stream.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise TaskError("host_image_limit", "image exceeds the intake byte budget")
    return content


async def load_native_image(locator, *, maximum_bytes=MAX_IMAGE_BYTES):
    """``maximum_bytes`` can only lower the image ceiling, e.g. to what a turn has left."""
    if not isinstance(locator, NativeImageLocator):
        raise TaskError("host_image_location_invalid", "image intake needs a frozen host component")
    maximum_bytes = min(maximum_bytes, MAX_IMAGE_BYTES)
    try:
        value = locator.locator
        if value.startswith("base64://"):
            encoded = value[len("base64://"):]
            if len(encoded) > (maximum_bytes + 2) // 3 * 4:
                raise TaskError("host_image_limit", "image exceeds the intake byte budget")
            content = base64.b64decode(encoded, validate=True)
        elif locator.host_resolved is True and Path(value).is_absolute():
            content = await asyncio.to_thread(_read_host_resolved_image, value, maximum_bytes)
        else:
            # Do not interpret a network message's file/path field as local disk authority.
            content = await fetch_image(value, maximum_bytes=maximum_bytes)
        if len(content) > maximum_bytes:
            raise TaskError("host_image_limit", "image exceeds the intake byte budget")
        return await asyncio.to_thread(inspect_image, content, locator.position)
    except TaskError:
        raise
    except Exception:
        raise TaskError("host_image_fetch_failed", "original image could not be prepared") from None
