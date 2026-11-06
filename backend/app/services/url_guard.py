"""One guard for every URL we contact on a customer's behalf.

Two endpoints let a signed-in user choose an address we then request from
inside our own network:

- `POST /channels/webchat/verify` fetches the agency's website (item 195);
- `POST /webhook-subscriptions/{id}/test` posts an event to the CRM address
  the agency configured (item 194).

Both are server-side request forgery primitives unless the target is
checked, and the check has to survive the three ways it is usually walked
around:

- a hostname that resolves to a private address — `localhost`, `10.x`, and
  above all `169.254.169.254`, which hands out the machine's cloud
  credentials to anything that can make it issue a GET;
- a hostname that resolves to *several* addresses, only one of them public;
- DNS rebinding: the name resolves to a public address while we check it and
  to a private one a millisecond later, when the HTTP client resolves it
  again. `PublicTarget.request_url` closes that by connecting to the address
  we validated and carrying the real host in the `Host` header (and in the
  TLS SNI, so certificate verification is unchanged).

The module is deliberately dependency-free so both the API layer and the
services layer can use it.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

# Ports a real customer website answers on. Applied to the webchat check,
# where anything else is either a typo or someone using us to knock on
# ports. Webhook targets do not get this restriction: a CRM behind a
# reverse proxy on an odd port is a normal setup.
WEB_PORTS = frozenset({80, 443, 8080, 8443})

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class PublicTarget:
    """A URL we have decided is safe to contact, and how to contact it."""

    url: str
    """Normalized URL, for logging and for resolving relative redirects."""

    host: str
    """Hostname as written. Goes in the `Host` header and in the TLS SNI."""

    host_header: str
    """`host` plus the port when the caller wrote one."""

    port: int
    scheme: str
    address: str
    """The validated IP we connect to."""

    addresses: tuple[str, ...]
    """Every address the host resolved to. All of them are public."""

    @property
    def request_url(self) -> str:
        """`url` with the validated IP in place of the hostname.

        Connecting here instead of to the name is what makes the check
        binding: a second DNS answer cannot move us to a private address
        between the check and the connection.
        """
        literal = f"[{self.address}]" if ":" in self.address else self.address
        parsed = urlparse(self.url)
        return urlunparse(parsed._replace(netloc=f"{literal}:{self.port}"))


def is_public_ip(host: str) -> bool:
    """True only for addresses on the public internet."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def normalize_url(url: str) -> str | None:
    """The URL with a scheme, or None when it is not one we may request.

    No DNS here: this is the part of the check that is cheap enough to run
    when a subscription is *created*, before anything is ever sent.
    """
    candidate = (url or "").strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    # `https://user:pass@host/` would hand those credentials to whatever
    # host we end up talking to. Never.
    if parsed.username or parsed.password:
        return None
    if not parsed.hostname:
        return None
    try:
        parsed.port  # noqa: B018 — raises on a malformed port
    except ValueError:
        return None
    return urlunparse(parsed)


def is_obviously_private(url: str) -> bool:
    """True when the URL names a private address *without* asking DNS.

    Used where blocking on a DNS lookup would be wrong (saving a webhook
    subscription whose CRM is not deployed yet): it catches the literal
    `http://127.0.0.1:8000/` and `http://169.254.169.254/` cases and lets
    every hostname through, to be resolved and checked at delivery time.
    """
    normalized = normalize_url(url)
    if normalized is None:
        return True
    host = urlparse(normalized).hostname or ""
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False  # a name, not a literal: decided later, with DNS
    return not is_public_ip(host)


async def _resolve(host: str) -> tuple[str, ...]:
    """Every address `host` answers with, in resolver order, deduplicated.

    Order is kept (rather than collapsed into a set) because the first entry
    is the one we connect to, and an arbitrary pick could land on an IPv6
    address on a host that has no IPv6 route.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, proto=socket.IPPROTO_TCP
        )
    except Exception:
        return ()
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in seen:
            seen.append(address)
    return tuple(seen)


async def resolve_public_target(
    url: str, *, allowed_ports: frozenset[int] | None = None
) -> PublicTarget | None:
    """The target we may contact, or None when we must not contact it.

    Every resolved address has to be public, not just the first: a hostname
    answering with one public and one private address would otherwise be a
    way in.
    """
    normalized = normalize_url(url)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    host = (parsed.hostname or "").strip("[]")
    port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    if allowed_ports is not None and port not in allowed_ports:
        return None

    addresses = await _resolve(host)
    if not addresses or not all(is_public_ip(addr) for addr in addresses):
        return None

    # The netloc as the caller wrote it, minus any userinfo (already
    # refused above): that is what the far end expects in `Host`.
    host_header = parsed.netloc.rsplit("@", 1)[-1]
    # IPv4 first when the host offers both: an IPv6-only attempt from a host
    # without an IPv6 route fails for a reason that has nothing to do with
    # the customer's website.
    address = next((a for a in addresses if ":" not in a), addresses[0])
    return PublicTarget(
        url=normalized,
        host=host,
        host_header=host_header,
        port=port,
        scheme=parsed.scheme,
        address=address,
        addresses=addresses,
    )
