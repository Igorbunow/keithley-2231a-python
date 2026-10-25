"""One SCPI command per newline. TCP is a raw transparent UART tunnel, NOT VISA."""
from __future__ import annotations
import socket
import threading
from typing import Protocol

class TransportError(IOError):
    pass

class Transport(Protocol):
    def write(self, line: str) -> None: ...
    def query(self, line: str) -> str: ...
    def close(self) -> None: ...

class SerialTransport:
    def __init__(self, port: str, *, baudrate: int = 9600, timeout: float = 2.0,
                 terminator: bytes = b"\r\n"):
        if timeout <= 0: raise ValueError("timeout must be positive")
        import serial
        self._serial = serial.Serial(port=port, baudrate=baudrate, bytesize=8,
                                     parity="N", stopbits=1, timeout=timeout,
                                     write_timeout=timeout, xonxoff=False,
                                     rtscts=False, dsrdtr=False)
        self._terminator = terminator
        self._lock = threading.RLock()

    def write(self, line: str) -> None:
        if '\n' in line or '\r' in line: raise ValueError("one command per line")
        with self._lock:
            try:
                self._serial.write(line.encode("ascii") + self._terminator)
                self._serial.flush()
            except Exception as exc:
                raise TransportError(f"serial write: {exc}") from exc

    def query(self, line: str) -> str:
        with self._lock:
            # Drain any unsolicited/stale input; prevents mistaking old replies for this query.
            self._serial.reset_input_buffer()
            self.write(line)
            try:
                reply = self._serial.readline()
            except Exception as exc:
                raise TransportError(f"serial read: {exc}") from exc
            if not reply.endswith(b"\n"):
                raise TransportError(f"incomplete/timeout reply to {line!r}: {reply!r}")
            try: return reply.decode("ascii").strip()
            except UnicodeDecodeError as exc: raise TransportError("non-ASCII reply") from exc

    def close(self) -> None:
        self._serial.close()

class TcpTransport:
    """Raw TCP-to-UART bridge; no commands/authentication/control negotiation.

    MCU must forward bytes transparently, enforce ONE client, and ideally watchdog
    connection health; beware that lost TCP does not necessarily disable PSU outputs.
    """
    def __init__(self, host: str, port: int, *, timeout: float = 2.0,
                 terminator: bytes = b"\r\n"):
        if not (1 <= port <= 65535) or timeout <= 0: raise ValueError("invalid TCP settings")
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._terminator = terminator
        self._lock = threading.RLock()

    def write(self, line: str) -> None:
        if '\n' in line or '\r' in line: raise ValueError("one command per line")
        with self._lock:
            try: self._sock.sendall(line.encode("ascii") + self._terminator)
            except OSError as exc: raise TransportError(f"TCP write failed: {exc}") from exc

    def query(self, line: str) -> str:
        with self._lock:
            # On each transaction, no abandoned response should be queued.
            if self._buf: raise TransportError("unsolicited/stale TCP input; reconnect")
            self.write(line)
            try:
                while b'\n' not in self._buf:
                    chunk = self._sock.recv(4096)
                    if not chunk: raise TransportError("TCP peer closed connection")
                    self._buf.extend(chunk)
                    if len(self._buf) > 4096: raise TransportError("oversized SCPI response")
                end = self._buf.index(b'\n') + 1
                reply = bytes(self._buf[:end]); del self._buf[:end]
                if self._buf: raise TransportError("unexpected extra TCP response; reconnect")
                return reply.decode("ascii").strip()
            except (OSError, UnicodeDecodeError) as exc:
                raise TransportError(f"TCP query failed: {exc}") from exc

    def close(self) -> None:
        self._sock.close()
