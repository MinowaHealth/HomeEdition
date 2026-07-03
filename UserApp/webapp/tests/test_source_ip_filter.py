"""Source-IP allowlist guard.

Home Edition must answer only the household LAN (RFC1918), CGNAT/Tailscale
(100.64.0.0/10), and loopback — never a public peer. This pins the exact
allowlist so it can't silently widen (e.g. by drifting back to
ipaddress.is_private, whose membership varies by Python version).
"""
import pytest

from app import _is_trusted_source


@pytest.mark.parametrize("addr", [
    "10.1.2.3",                 # RFC1918 10/8
    "172.16.5.5",               # RFC1918 172.16/12
    "172.31.255.254",           # RFC1918 172.16/12 upper edge
    "192.168.1.1",              # RFC1918 192.168/16
    "100.64.9.9",               # CGNAT / Tailscale
    "127.0.0.1",                # IPv4 loopback
    "::1",                      # IPv6 loopback
    "fd7a:115c:a1e0::1",        # IPv6 ULA (Tailscale v6)
    "::ffff:192.168.1.5",       # IPv4-mapped IPv6 of a LAN host
])
def test_trusted_sources_allowed(addr):
    assert _is_trusted_source(addr) is True


@pytest.mark.parametrize("addr", [
    "8.8.8.8",                  # public IPv4
    "172.32.0.1",              # just outside 172.16/12
    "169.254.1.1",              # link-local — deliberately NOT trusted
    "100.128.0.1",              # just outside CGNAT 100.64/10
    "2607:f8b0::1",             # public IPv6
    "::ffff:8.8.8.8",           # IPv4-mapped IPv6 of a PUBLIC host (spoof attempt)
    None,                        # missing remote_addr
    "garbage",                   # unparseable
])
def test_untrusted_sources_blocked(addr):
    assert _is_trusted_source(addr) is False


if __name__ == "__main__":
    assert _is_trusted_source("192.168.1.1")
    assert _is_trusted_source("100.64.0.1")
    assert not _is_trusted_source("8.8.8.8")
    assert not _is_trusted_source("::ffff:8.8.8.8")
    print("source-ip allowlist OK")
