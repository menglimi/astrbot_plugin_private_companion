"""Transport evidence for calls that could not reach the provider."""

from http import HTTPStatus

from httpx import ConnectError, ConnectTimeout, PoolTimeout, UnsupportedProtocol


def definitely_not_sent(error):
    """Read typed causes, never provider text, to prove no request was sent.

    Read/write timeouts, generic connection errors and HTTP responses do not
    provide this proof. Their reservations must remain held for accounting.
    """
    seen = set()
    while isinstance(error, Exception) and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (ConnectError, ConnectTimeout, PoolTimeout, UnsupportedProtocol)):
            return True
        error = error.__cause__
    return False


_HTTP_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 405, 415, 422})


def _valid_http_status(value):
    """Return a plain HTTP status int (100..599), rejecting bool/None/strings."""
    if type(value) is int:
        return value if 100 <= value <= 599 else None
    if isinstance(value, HTTPStatus):
        code = int(value)
        return code if 100 <= code <= 599 else None
    return None


def _direct_error_status_code(error):
    """Extract a validated HTTP status code directly from one error object."""
    if error is None:
        return None
    # aiohttp.ClientResponseError exposes .status directly and emits a
    # DeprecationWarning when .code is accessed; inspect .status without .code.
    is_aiohttp = type(error).__module__.startswith("aiohttp.")
    attrs = ("status_code", "http_status", "status") if is_aiohttp else ("status_code", "code", "http_status", "status")
    for attr in attrs:
        status = _valid_http_status(getattr(error, attr, None))
        if status is not None:
            return status
    response = getattr(error, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            status = _valid_http_status(getattr(response, attr, None))
            if status is not None:
                return status
    return None


def extract_error_status_code(error):
    """Return a valid HTTP status code (100..599) from an error or its cause chain."""
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = _direct_error_status_code(current)
        if status is not None:
            return status
        current = getattr(current, "__cause__", None)
    return None


def rejected_before_generation(error):
    """Return true for HTTP responses that reject a request before generation.

    These are typed client-side rejections (bad request, auth, unsupported
    media), not transport timeouts. Rate-limit responses can be ambiguous about
    whether provider processing began. No completion was produced, so
    the caller may safely mark the model call as not started and avoid leaving
    a reflection batch permanently ``running``.
    """
    seen = set()
    while isinstance(error, Exception) and id(error) not in seen:
        seen.add(id(error))
        status = _direct_error_status_code(error)
        if status in _HTTP_REJECTION_STATUSES:
            return True
        error = error.__cause__
    return False
