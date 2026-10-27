"""Sprint 11 egress allowlist proxy (HTTP CONNECT only, exact host and port match).

The editor container sits on an internal Docker network whose only route out is
this proxy. It tunnels TLS for allowlisted host:port pairs and refuses everything
else: plain HTTP, other ports, IP literals, and hosts not on the list. It never
terminates TLS, so it sees host names, not content. One JSON line per request
goes to stdout; the launcher keeps it as the session's egress log.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import sys
import time

ALLOWLIST_PATH = os.environ.get("ALLOWLIST", "/etc/proxy/allowlist.txt")
LISTEN_PORT = int(os.environ.get("PORT", "3128"))
MAX_HEADER = 16384


def load_allowlist(path):
    pairs = set()
    with open(path, encoding="utf-8") as stream:
        for raw in stream:
            line = raw.split("#", 1)[0].strip().lower()
            if line:
                host, _, port = line.partition(":")
                pairs.add((host, int(port or 443)))
    return frozenset(pairs)


ALLOW = load_allowlist(ALLOWLIST_PATH)


def log(**record):
    record["time"] = round(time.time(), 3)
    sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stdout.flush()


def is_ip(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    except Exception:
        writer.close()
        return
    if len(head) > MAX_HEADER:
        writer.close()
        return
    request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = request_line.split()
    method = parts[0].upper() if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    host, _, port = target.rpartition(":")
    host = host.lower()
    allowed = (method == "CONNECT" and port.isdigit() and not is_ip(host) and (host, int(port)) in ALLOW)
    log(peer=str(peer[0]) if peer else None, method=method, target=target if method == "CONNECT" else target[:200],
        allowed=allowed)
    if not allowed:
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=15)
    except Exception as error:
        log(target=target, upstream_error=type(error).__name__)
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    log(event="listening", port=LISTEN_PORT, allowlist=sorted(f"{h}:{p}" for h, p in ALLOW))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
