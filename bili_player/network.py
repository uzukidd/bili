"""Per-request proxy routing for the player's HTTP traffic."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

import requests
from requests.utils import get_environ_proxies, select_proxy

_INVALID_URL = "代理地址必须是带端口的 HTTP 或 HTTPS 地址，且不能包含用户名或密码"
_proxy = ("system", "")


def validate_proxy_url(url: str) -> str:
    """Validate a manual HTTP(S) proxy and return its normalized address."""
    if not isinstance(url, str):
        raise ValueError(_INVALID_URL)
    value = url.strip()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(_INVALID_URL)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in ("http", "https") or not host or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or "?" in value or "#" in value):
            raise ValueError
        if "[" in parsed.netloc:
            if not re.fullmatch(r"\[[0-9A-Fa-f:.]+\]:[0-9]+", parsed.netloc):
                raise ValueError
            ipaddress.IPv6Address(host)
            host = f"[{host}]"
        else:
            host = host.encode("idna").decode("ascii")
            labels = host.removesuffix(".").split(".")
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in labels):
                raise ValueError
    except ValueError:
        raise ValueError(_INVALID_URL) from None
    return f"{parsed.scheme}://{host}:{parsed.port}"


def set_proxy(mode: str, url: str = "") -> None:
    """Apply settings to subsequent requests without modifying the environment."""
    global _proxy
    if mode not in ("system", "direct", "manual"):
        raise ValueError("不支持的代理模式")
    _proxy = (mode, validate_proxy_url(url) if mode == "manual" else "")


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            # The OS also accepts abbreviated and integer IPv4 addresses.
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def proxy_for_url(url: str) -> str | None:
    """Return the effective proxy URL, or None when the destination is direct."""
    mode, manual = _proxy
    if mode == "direct" or _is_loopback(url):
        return None
    if mode == "manual":
        return manual
    return select_proxy(url, get_environ_proxies(url))


def extractor_proxy() -> str | None:
    """Match yt-dlp's automatic (None), direct (empty), and explicit options."""
    mode, manual = _proxy
    return None if mode == "system" else manual


class Session(requests.Session):
    """A Requests session that also re-evaluates proxy routing on redirects."""

    def send(self, request, **kwargs):
        proxy = proxy_for_url(request.url)
        kwargs["proxies"] = {urlsplit(request.url).scheme: proxy} if proxy else {}
        return super().send(request, **kwargs)

    def rebuild_proxies(self, prepared_request, _proxies):
        # The adapter applies proxy credentials as needed. Drop the previous hop's
        # header and let send() independently choose the route for the next hop.
        prepared_request.headers.pop("Proxy-Authorization", None)
        return {}


def request(method: str, url: str, **kwargs) -> requests.Response:
    with Session() as session:
        return session.request(method, url, **kwargs)


def get(url: str, **kwargs) -> requests.Response:
    return request("GET", url, **kwargs)
