"""Pytest plugin that blocks outbound Python socket traffic in offline CI.

Loopback TCP connections remain available for local HTTP tests. Unix-domain
sockets are also allowed. This is a process-level Python socket guard, not an
OS firewall: subprocesses and non-socket egress mechanisms are outside its
coverage. The CI job has no provider credentials, and the guard is activated
only when PHOTONGUARD_CI_OFFLINE=1 is present.
"""

from __future__ import annotations

import errno
import ipaddress
import os
import socket
from typing import Any

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_ORIGINAL_SENDTO = socket.socket.sendto
_ORIGINAL_SENDMSG = getattr(socket.socket, "sendmsg", None)


def is_loopback_host(host: Any) -> bool:
    """Return True only for localhost names and IP loopback addresses."""
    if not isinstance(host, str):
        return False
    normalized = host.strip().strip("[]").split("%", 1)[0].lower()
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_allowed_address(address: Any) -> bool:
    """Allow loopback network addresses and local Unix-domain socket paths."""
    if isinstance(address, tuple):
        return bool(address) and is_loopback_host(address[0])
    return isinstance(address, (str, bytes))


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    """Prevent DNS lookups for non-local hosts in offline CI."""
    if host is not None and not is_loopback_host(str(host)) and str(host) not in {"0.0.0.0", "::"}:
        raise OSError(errno.EPERM, "External DNS/network access is disabled in PhotonGuard CI")
    return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)


def _guarded_connect(sock: socket.socket, address: Any) -> Any:
    if not is_allowed_address(address):
        raise OSError(errno.EPERM, "Outbound network access is disabled in PhotonGuard CI")
    return _ORIGINAL_CONNECT(sock, address)


def _guarded_connect_ex(sock: socket.socket, address: Any) -> int:
    if not is_allowed_address(address):
        return errno.EPERM
    return _ORIGINAL_CONNECT_EX(sock, address)


def _guarded_sendto(sock: socket.socket, data: Any, *args: Any) -> Any:
    """Block UDP datagrams addressed to non-local hosts."""
    address = args[-1] if args else None
    if address is not None and not is_allowed_address(address):
        raise OSError(errno.EPERM, "Outbound network access is disabled in PhotonGuard CI")
    return _ORIGINAL_SENDTO(sock, data, *args)


def _guarded_sendmsg(sock: socket.socket, buffers: Any, *args: Any, **kwargs: Any) -> Any:
    """Block sendmsg datagrams addressed to non-local hosts."""
    address = kwargs.get("address")
    if address is None and len(args) >= 3:
        address = args[2]
    if address is not None and not is_allowed_address(address):
        raise OSError(errno.EPERM, "Outbound network access is disabled in PhotonGuard CI")
    if _ORIGINAL_SENDMSG is None:
        raise OSError(errno.ENOTSUP, "sendmsg is unavailable on this platform")
    return _ORIGINAL_SENDMSG(sock, buffers, *args, **kwargs)


def pytest_configure(config: Any) -> None:
    """Install the outbound-network guard only in explicitly offline CI."""
    if os.environ.get("PHOTONGUARD_CI_OFFLINE") != "1":
        return
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]
    socket.socket.sendto = _guarded_sendto  # type: ignore[method-assign]
    if _ORIGINAL_SENDMSG is not None:
        socket.socket.sendmsg = _guarded_sendmsg  # type: ignore[method-assign]
