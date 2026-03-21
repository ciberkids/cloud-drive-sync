"""Proxy configuration helpers."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from cloud_drive_sync.util.logging import get_logger

log = get_logger("util.proxy")


def parse_proxy_url(url: str):
    """Parse a proxy URL into an httplib2.ProxyInfo instance.

    Returns None if the URL is empty or invalid, or if httplib2 is not available.
    """
    if not url:
        return None

    try:
        import httplib2
        import socks
    except ImportError:
        log.warning("httplib2/PySocks not available, proxy support disabled")
        return None

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    proxy_type_map = {
        "http": socks.PROXY_TYPE_HTTP,
        "https": socks.PROXY_TYPE_HTTP,
        "socks4": socks.PROXY_TYPE_SOCKS4,
        "socks5": socks.PROXY_TYPE_SOCKS5,
    }

    proxy_type = proxy_type_map.get(scheme, socks.PROXY_TYPE_HTTP)
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 8080)
    user = parsed.username
    password = parsed.password

    return httplib2.ProxyInfo(
        proxy_type=proxy_type,
        proxy_host=host,
        proxy_port=port,
        proxy_user=user,
        proxy_pass=password,
    )


def apply_env_proxy(config):
    """Fill in empty proxy fields from environment variables.

    Reads HTTP_PROXY, HTTPS_PROXY, and NO_PROXY (case-insensitive).
    Returns the (possibly updated) ProxyConfig.
    """
    if not config.http_proxy:
        config.http_proxy = os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", ""))
    if not config.https_proxy:
        config.https_proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
    if not config.no_proxy:
        config.no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
    return config
