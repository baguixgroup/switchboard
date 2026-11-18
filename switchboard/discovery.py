"""Find the hub on the local network, so setting up a new computer needs no IP address.

The hub answers a small UDP broadcast on its port (8765 by default, UDP, so it doesn't clash with
the dashboard's TCP port). The answer only says "a Switchboard hub named X lives here": joining still
needs a pairing code or the token.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import socket
import time

from . import __version__

log = logging.getLogger("switchboard.discovery")
PORT = 8765
ASK = b"SWITCHBOARD-DISCOVER 1"


def answer(name: str, port: int) -> bytes:
    return json.dumps({"switchboard": 1, "name": name, "port": port, "version": __version__}).encode()


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, name: str, port: int):
        self.reply = answer(name, port)
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if data.strip() == ASK and self.transport:
            self.transport.sendto(self.reply, addr)


async def start_responder(name: str, http_port: int, udp_port: int = PORT):
    """Answer discovery broadcasts. Returns the transport (close it on shutdown), or None if the port is taken."""
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Responder(name, http_port), local_addr=("0.0.0.0", udp_port), allow_broadcast=True)
        return transport
    except OSError as e:
        log.warning("LAN discovery is off (UDP %s: %s)", udp_port, e)
        return None


def _broadcast_targets() -> list[str]:
    """255.255.255.255 plus the /24 broadcast of each LAN address (some routers drop the global one)."""
    out = ["255.255.255.255"]
    ips: set[str] = set()
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))  # sends nothing; picks the LAN interface
        ips.add(s.getsockname()[0])
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    for ip in ips:
        with contextlib.suppress(ValueError):
            a = ipaddress.ip_address(ip)
            if a.is_private and not a.is_loopback:
                out.append(str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address))
    return out


def find_hubs(timeout: float = 1.5, udp_port: int = PORT, targets: list[str] | None = None) -> list[dict]:
    """Broadcast and collect answers: [{"name", "url", "version"}], one per hub."""
    found: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
        for t in targets or _broadcast_targets():
            with contextlib.suppress(OSError):
                s.sendto(ASK, (t, udp_port))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, (ip, _) = s.recvfrom(2048)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            with contextlib.suppress(ValueError, TypeError):
                d = json.loads(data)
                if d.get("switchboard") == 1:
                    url = f"http://{ip}:{int(d.get('port', 8765))}"
                    found[url] = {"name": str(d.get("name", ""))[:64], "url": url, "version": str(d.get("version", ""))[:32]}
    return sorted(found.values(), key=lambda h: h["name"])
