"""One SCPI command per newline over serial or raw TCP, without VISA negotiation."""
from __future__ import annotations
import socket
import os
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
                                     rtscts=False, dsrdtr=False,
                                     **({'exclusive': True} if os.name == 'posix' else {}))
        self._terminator = terminator
        self._lock = threading.RLock()
        self._failed = False
        self._debug_status_trace = None

    def set_debug_status_trace(self, observer):
        """Install a driver-controlled status-byte observer; None disables it."""
        if observer is not None and not callable(observer):
            raise TypeError('status observer must be callable or None')
        with self._lock: self._debug_status_trace = observer

    def _trace_status(self, event, command, payload):
        if self._debug_status_trace is not None and command.upper().startswith(('STAT:', 'STATUS:')):
            field = 'tx_hex' if event == 'status_wire_write' else 'rx_hex'
            self._debug_status_trace(dict(event=event, command=command, **{field: payload.hex(' ')}))

    def write(self, line: str) -> None:
        if '\n' in line or '\r' in line: raise ValueError("one command per line")
        with self._lock:
            if self._failed: raise TransportError('serial link uncertain; close and requalify before reconnecting')
            try:
                message = line.encode("ascii") + self._terminator
                self._trace_status('status_wire_write', line, message)
                if self._serial.write(message) != len(message):
                    raise TransportError('short serial write')
                self._serial.flush()
            except Exception as exc:
                self._failed = True
                raise TransportError(f"serial write: {exc}") from exc

    def query(self, line: str) -> str:
        with self._lock:
            if self._failed: raise TransportError('serial link uncertain; close and requalify before reconnecting')
            try:
                # Discarding bytes cannot rule out an old response still in flight.
                if self._serial.in_waiting:
                    raise TransportError('unsolicited/stale serial input')
                self.write(line)
                reply = self._serial.readline(4097)
                self._trace_status('status_wire_read', line, reply)
                if not reply.endswith(b"\n") or len(reply) > 4096:
                    raise TransportError('incomplete/oversized/timeout reply')
                return reply.decode("ascii").strip()
            except Exception as exc:
                self._failed = True
                raise TransportError(f"serial query {line!r} failed; UNKNOWN OUTPUT STATE") from exc

    def close(self) -> None:
        self._serial.close()

class TcpTransport:
    """Line-oriented raw TCP endpoint or transparent UART bridge; no negotiation.

    A UART bridge must forward bytes transparently, enforce ONE client, and ideally watchdog
    connection health; beware that lost TCP does not necessarily disable PSU outputs.
    Native raw-SCPI instruments can use their documented port and terminator.
    """
    def __init__(self, host: str, port: int, *, timeout: float = 2.0,
                 terminator: bytes = b"\r\n"):
        if not (1 <= port <= 65535) or timeout <= 0: raise ValueError("invalid TCP settings")
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._terminator = terminator
        self._lock = threading.RLock()
        self._failed = False

    def write(self, line: str) -> None:
        if '\n' in line or '\r' in line: raise ValueError("one command per line")
        with self._lock:
            if self._failed: raise TransportError('TCP link uncertain; close and requalify before reconnecting')
            try: self._sock.sendall(line.encode("ascii") + self._terminator)
            except OSError as exc:
                self._failed = True
                raise TransportError(f"TCP write failed: {exc}") from exc

    def query(self, line: str) -> str:
        with self._lock:
            if self._failed: raise TransportError('TCP link uncertain; close and requalify before reconnecting')
            try:
                if self._buf: raise TransportError("unsolicited/stale TCP input; reconnect")
                self.write(line)
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
                self._failed = True
                raise TransportError(f"TCP query failed: {exc}") from exc

    def close(self) -> None:
        self._sock.close()
