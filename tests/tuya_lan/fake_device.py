"""A simulated Tuya Wi-Fi device speaking the LAN protocol (3.1, 3.3, 3.4, 3.5).

Implements the device side of the framing, encryption and 3.4/3.5 session key
negotiation with tinytuya's primitives, so the client is exercised against
real encrypted traffic. Behaviour follows tinytuya's client expectations and
public protocol descriptions; it is a test double, not a reference device.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import struct
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

import tinytuya
from tinytuya.core import command_types as CT
from tinytuya.core import header as H

VERSION_HEADER_PAD = H.PROTOCOL_3x_HEADER  # 12 zero bytes after the version


@dataclass
class _Session:
    writer: asyncio.StreamWriter
    key: bytes
    local_nonce: bytes = b""
    remote_nonce: bytes = b""
    seqno: int = 1


@dataclass
class FakeTuyaDevice:
    device_id: str
    local_key: str
    version: str = "3.3"
    dps: dict[str, Any] = field(default_factory=dict)
    host: str = "127.0.0.1"
    product_key: str = "a2zf7q0zxcutn5dy"
    # Reply to control requests with a status push (real devices do).
    push_on_control: bool = True
    # Stay silent instead of answering (simulates a hung device).
    silent: bool = False
    # Stop answering heartbeats only.
    ignore_heartbeats: bool = False

    def __post_init__(self) -> None:
        self.port: int = 0
        self.received: list[dict[str, Any]] = []
        self.connections_total = 0
        self._server: asyncio.Server | None = None
        self._sessions: list[_Session] = []
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def real_key(self) -> bytes:
        return self.local_key.encode("latin1")

    @property
    def v(self) -> float:
        return float(self.version)

    @property
    def open_connections(self) -> int:
        return len(self._sessions)

    # ---------------------------------------------------------- lifecycle

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        await self.drop_connections()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def drop_connections(self) -> None:
        """Close all client connections (like a device reboot)."""
        for session in list(self._sessions):
            session.writer.close()
            with contextlib.suppress(Exception):
                await session.writer.wait_closed()
        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._sessions.clear()

    async def push(self, dps: dict[str, Any]) -> None:
        """Change DPs on the device and report them to connected clients."""
        self.dps.update(dps)
        for session in list(self._sessions):
            with contextlib.suppress(ConnectionError):
                await self._send_status(session, dps)

    # -------------------------------------------------------- connection

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        session = _Session(writer, self.real_key)
        self._sessions.append(session)
        self.connections_total += 1
        try:
            while True:
                data = await self._read_frame(reader)
                if data is None:
                    break
                if not await self._dispatch(session, data):
                    break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if session in self._sessions:
                self._sessions.remove(session)
            self._tasks.discard(task)
            writer.close()

    @staticmethod
    async def _read_frame(reader: asyncio.StreamReader) -> bytes | None:
        prefix = await reader.read(4)
        if len(prefix) < 4:
            return None
        if prefix == H.PREFIX_6699_BIN:
            rest = await reader.readexactly(struct.calcsize(H.MESSAGE_HEADER_FMT_6699) - 4)
        elif prefix == H.PREFIX_55AA_BIN:
            rest = await reader.readexactly(struct.calcsize(H.MESSAGE_HEADER_FMT_55AA) - 4)
        else:
            return None
        header = tinytuya.parse_header(prefix + rest)
        body = await reader.readexactly(header.total_length - len(prefix) - len(rest))
        return prefix + rest + body

    # ------------------------------------------------------------ decode

    def _unpack(self, session: _Session, data: bytes) -> tinytuya.TuyaMessage | None:
        hmac_key = session.key if self.v >= 3.4 else None
        expected = H.PREFIX_6699_BIN if self.v >= 3.5 else H.PREFIX_55AA_BIN
        if not data.startswith(expected):
            return None  # a client speaking another protocol version
        try:
            msg = tinytuya.unpack_message(data, hmac_key=hmac_key, no_retcode=True)
        except Exception:
            return None
        return msg if msg.crc_good else None

    def _decrypt(self, session: _Session, cmd: int, payload: bytes) -> bytes:
        cipher = tinytuya.AESCipher(session.key)
        if self.v >= 3.5:
            plain = payload
        elif self.v >= 3.4:
            plain = cipher.decrypt(payload, False, decode_text=False, verify_padding=True)
        elif self.v >= 3.2:
            if payload.startswith(self.version.encode()):
                payload = payload[len(self.version) + len(VERSION_HEADER_PAD) :]
            plain = cipher.decrypt(payload, False, decode_text=False, verify_padding=True)
        else:  # 3.1: only control requests are encrypted
            if payload.startswith(H.PROTOCOL_VERSION_BYTES_31):
                plain = cipher.decrypt(payload[3 + 16 :], decode_text=False, verify_padding=True)
            else:
                plain = payload
        version = self.version.encode()
        if plain.startswith(version) and cmd not in H.NO_PROTOCOL_HEADER_CMDS:
            plain = plain[len(version) + len(VERSION_HEADER_PAD) :]
        return plain

    # ---------------------------------------------------------- dispatch

    async def _dispatch(self, session: _Session, data: bytes) -> bool:
        """Handle one frame; False closes the connection."""
        msg = self._unpack(session, data)
        if msg is None:
            return False  # wrong key (HMAC/GCM tag mismatch): drop the client
        if msg.cmd == CT.SESS_KEY_NEG_START:
            return await self._negotiate_start(session, msg.payload)
        if msg.cmd == CT.SESS_KEY_NEG_FINISH:
            return self._negotiate_finish(session, msg.payload)
        try:
            plain = self._decrypt(session, msg.cmd, msg.payload)
            request = json.loads(plain) if plain else {}
        except Exception:  # undecryptable: wrong local key
            await self._send(session, msg.cmd, b"data format error")
            return True
        self.received.append({"cmd": msg.cmd, "request": request})
        if self.silent:
            return True
        if msg.cmd in (CT.DP_QUERY, CT.DP_QUERY_NEW):
            await self._send(session, msg.cmd, self._status_body(self.dps, query=True))
        elif msg.cmd == CT.HEART_BEAT:
            if not self.ignore_heartbeats:
                await self._send(session, CT.HEART_BEAT, b"")
        elif msg.cmd in (CT.CONTROL, CT.CONTROL_NEW):
            dps = request.get("dps") or request.get("data", {}).get("dps") or {}
            self.dps.update(dps)
            await self._send(session, msg.cmd, b"")
            if self.push_on_control and dps:
                for other in list(self._sessions):
                    with contextlib.suppress(ConnectionError):
                        await self._send_status(other, dps)
        elif msg.cmd == CT.UPDATEDPS:
            await self._send(session, CT.UPDATEDPS, b"")
        return True

    async def _negotiate_start(self, session: _Session, payload: bytes) -> bool:
        cipher = tinytuya.AESCipher(self.real_key)
        if self.v < 3.5:
            try:
                payload = cipher.decrypt(payload, False, decode_text=False)
            except ValueError:
                return False
        session.local_nonce = payload[:16]
        session.remote_nonce = os.urandom(16)
        proof = hmac.new(self.real_key, session.local_nonce, sha256).digest()
        answer = session.remote_nonce + proof
        await self._send(session, CT.SESS_KEY_NEG_RESP, answer, plain_header=False)
        return True

    def _negotiate_finish(self, session: _Session, payload: bytes) -> bool:
        expected = hmac.new(self.real_key, session.remote_nonce, sha256).digest()
        if self.v < 3.5:
            payload = tinytuya.AESCipher(session.key).decrypt(payload, False, decode_text=False)
        if payload[:32] != expected:
            return False
        xored = bytes(a ^ b for a, b in zip(session.local_nonce, session.remote_nonce, strict=True))
        cipher = tinytuya.AESCipher(self.real_key)
        if self.v >= 3.5:
            session.key = cipher.encrypt(
                xored, use_base64=False, pad=False, iv=session.local_nonce[:12]
            )[12:28]
        else:
            session.key = cipher.encrypt(xored, False, pad=False)
        return True

    # ------------------------------------------------------------ encode

    def _status_body(self, dps: dict[str, Any], *, query: bool = False) -> bytes:
        if self.v >= 3.4 and not query:
            body = {"protocol": 4, "t": int(time.time()), "data": {"dps": dps}}
        else:
            body = {"devId": self.device_id, "dps": dps}
        return json.dumps(body, separators=(",", ":")).encode()

    async def _send_status(self, session: _Session, dps: dict[str, Any]) -> None:
        await self._send(session, CT.STATUS, self._status_body(dps), plain_header=True)

    async def _send(
        self,
        session: _Session,
        cmd: int,
        body: bytes,
        *,
        plain_header: bool = False,
    ) -> None:
        """Encrypt and frame ``body``. ``plain_header``: prefix the version header."""
        cipher = tinytuya.AESCipher(session.key)
        version_header = self.version.encode() + VERSION_HEADER_PAD
        if self.v >= 3.5:
            payload = (version_header + body) if plain_header and body else body
            msg = tinytuya.TuyaMessage(
                session.seqno, cmd, 0, payload, 0, True, H.PREFIX_6699_VALUE, True
            )
            frame = tinytuya.pack_message(msg, hmac_key=session.key)
        else:
            if not body:
                payload = b""
            elif self.v >= 3.4:
                inner = (version_header + body) if plain_header else body
                payload = cipher.encrypt(inner, False)
            elif self.v >= 3.2:
                payload = cipher.encrypt(body, False)
                if plain_header:
                    payload = version_header + payload
            else:
                payload = body  # 3.1 reports in plain JSON
            hmac_key = session.key if self.v >= 3.4 else None
            msg = tinytuya.TuyaMessage(
                session.seqno, cmd, 0, struct.pack(">I", 0) + payload, 0, True, H.PREFIX_55AA_VALUE
            )
            frame = tinytuya.pack_message(msg, hmac_key=hmac_key)
        session.seqno += 1
        session.writer.write(frame)
        await session.writer.drain()

    # --------------------------------------------------------- discovery

    def broadcast_packet(self, ip: str | None = None) -> bytes:
        """The UDP announcement this device would broadcast."""
        body = json.dumps(
            {
                "ip": ip or self.host,
                "gwId": self.device_id,
                "active": 2,
                "ability": 0,
                "mode": 0,
                "encrypt": True,
                "productKey": self.product_key,
                "version": self.version,
            }
        ).encode()
        if self.v >= 3.5:
            msg = tinytuya.TuyaMessage(0, CT.UDP_NEW, 0, body, 0, True, H.PREFIX_6699_VALUE, True)
            return tinytuya.pack_message(msg, hmac_key=tinytuya.udpkey)
        payload = body if self.v < 3.2 else tinytuya.AESCipher(tinytuya.udpkey).encrypt(body, False)
        msg = tinytuya.TuyaMessage(0, CT.UDP_NEW, 0, struct.pack(">I", 0) + payload, 0, True)
        return tinytuya.pack_message(msg)
