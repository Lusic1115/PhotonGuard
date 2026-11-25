"""Tests for the CI-only outbound-network guard; no sockets are opened."""

import errno
import os
import socket

import pytest

from photonguard_ci_network_guard import (
    _guarded_connect,
    _guarded_connect_ex,
    _guarded_getaddrinfo,
    _guarded_sendto,
    _guarded_sendmsg,
    is_allowed_address,
    is_loopback_host,
)


@pytest.mark.parametrize("host", ["localhost", "localhost.localdomain", "127.0.0.1", "127.23.4.5", "::1", "[::1]"])
def test_loopback_hosts_are_recognized(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["example.com", "api.deepseek.com", "203.0.113.10", "2001:db8::1", "0.0.0.0"])
def test_non_loopback_hosts_are_not_allowed(host):
    assert not is_loopback_host(host)
    assert not is_allowed_address((host, 443))


def test_unix_domain_socket_address_is_allowed():
    assert is_allowed_address("/tmp/photonguard-test.sock")


def test_unknown_non_tuple_address_is_rejected():
    assert not is_allowed_address(None)
    assert not is_allowed_address(123)


def test_guarded_connect_rejects_external_address_before_using_socket():
    with pytest.raises(OSError, match="Outbound network access is disabled"):
        _guarded_connect(None, ("api.deepseek.com", 443))


def test_guarded_connect_ex_returns_permission_error_for_external_address():
    assert _guarded_connect_ex(None, ("api.deepseek.com", 443)) == errno.EPERM


def test_guarded_getaddrinfo_rejects_external_dns_before_resolution():
    with pytest.raises(OSError, match="External DNS/network access is disabled"):
        _guarded_getaddrinfo("api.deepseek.com", 443)


def test_offline_ci_environment_activates_global_socket_guards():
    if os.environ.get("PHOTONGUARD_CI_OFFLINE") == "1":
        assert socket.socket.connect is _guarded_connect
        assert socket.socket.connect_ex is _guarded_connect_ex
        assert socket.socket.sendto is _guarded_sendto
        with pytest.raises(OSError, match="External DNS/network access is disabled"):
            socket.getaddrinfo("api.deepseek.com", 443)


def test_guarded_sendto_rejects_external_udp_before_using_socket():
    with pytest.raises(OSError, match="Outbound network access is disabled"):
        _guarded_sendto(None, b"test", ("api.deepseek.com", 53))


def test_guarded_sendmsg_rejects_external_udp_before_using_socket():
    with pytest.raises(OSError, match="Outbound network access is disabled"):
        _guarded_sendmsg(None, [b"test"], [], 0, ("api.deepseek.com", 53))
