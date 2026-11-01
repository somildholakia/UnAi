from __future__ import annotations

import hmac
import ipaddress


def api_access_error(
    client_host: str | None,
    authorization: str | None,
    configured_token: str,
) -> tuple[int, str] | None:
    """Return an HTTP denial, or None when the request passes the API perimeter.

    A configured token is required for every request. Without one, only loopback
    clients are accepted; forwarded-IP headers are deliberately not trusted.
    """
    if configured_token:
        scheme, separator, presented = (authorization or "").partition(" ")
        valid = (
            scheme.casefold() == "bearer"
            and bool(separator)
            and bool(presented)
            and hmac.compare_digest(
                presented.encode("utf-8"),
                configured_token.encode("utf-8"),
            )
        )
        if not valid:
            return 401, "A valid bearer token is required."
        return None

    # Starlette's in-process TestClient uses this synthetic peer name.
    if client_host == "testclient":
        return None
    try:
        is_loopback = ipaddress.ip_address(client_host or "").is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        return 403, "UNAI_API_TOKEN must be configured for non-loopback access."
    return None
