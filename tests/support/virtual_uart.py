"""Real POSIX pseudo-terminal pair. NOT a service simulator or instrument model.

The peer only responds to a strict canned test protocol and records raw ASCII
lines; tests exercise the production Keithley2231A dispatcher over a character
stream. No pyserial/physical device required.
"""
from __future__ import annotations
import os
import select
import tty
import threading


class PTYTransport:
    """Thin test-only Transport over the slave side of a POSIX virtual UART."""
    def __init__(self, path, timeout=.35):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.timeout = timeout
        self.failed = False
        self.closed = False
        tty.setraw(self.fd)

    def write(self, line):
        if self.failed: raise OSError('uncertain test UART')
        if '\r' in line or '\n' in line: raise ValueError('invalid line')
        try: os.write(self.fd, (line + '\r\n').encode('ascii'))
        except BaseException:
            self.failed = True
            raise

    def query(self, line):
        self.write(line)
        data = bytearray()
        try:
            while len(data) <= 4096:
                ready, _, _ = select.select([self.fd], [], [], self.timeout)
                if not ready: raise TimeoutError('virtual UART response timeout')
                try: chunk = os.read(self.fd, 4096)
                except BlockingIOError: continue
                if not chunk: raise EOFError('virtual UART peer closed')
                data.extend(chunk)
                if b'\n' in data:
                    if data.count(b'\n') != 1 or data.index(b'\n') != len(data)-1:
                        raise IOError('unexpected extra response bytes')
                    return data.decode('ascii').strip()
            raise IOError('oversized response')
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self.fd)


class UARTPeer:
    """Canned protocol fixture; no independent driver or service state machine."""
    def __init__(self, *, serial='LAB-UNIT-001', response_overrides=None, drop=None,
                 fault_code=None, off_by_channel=None):
        self.master, slave = os.openpty()
        tty.setraw(self.master)
        tty.setraw(slave)
        self.path = os.ttyname(slave)
        self._slave_guard = slave  # keep peer valid until test transport opens
        self.serial = serial
        self.rx = []
        self.rx_bytes = []
        self.overrides = response_overrides or {}
        self.drop = drop
        self.fault_code = fault_code
        self.off_by_channel = off_by_channel or {}
        self.selected = 1
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        buf = bytearray()
        while not self.stop.is_set():
            try:
                ready, _, _ = select.select([self.master], [], [], .05)
                if not ready: continue
                raw = os.read(self.master, 4096)
                if not raw: continue
            except (OSError, ValueError):
                return
            buf.extend(raw)
            while b'\n' in buf:
                raw_line, _, rest = buf.partition(b'\n')
                buf = bytearray(rest)
                self.rx_bytes.append(bytes(raw_line) + b'\n')
                cmd = raw_line.rstrip(b'\r').decode('ascii')
                self.rx.append(cmd)
                if cmd.startswith('INST:NSEL '):
                    self.selected = int(cmd[-1])
                if not cmd.endswith('?') or cmd == self.drop:
                    continue
                if cmd in self.overrides:
                    response = self.overrides[cmd]
                elif cmd == '*IDN?':
                    response = f'Keithley instruments, 2231A-30-3, {self.serial}, 1.04-1.04'
                elif cmd == 'INST:NSEL?': response = str(self.selected)
                elif cmd == 'CHAN:OUTP?': response = str(self.off_by_channel.get(self.selected, 0))
                elif cmd == 'SYST:ERR?': response = self.fault_code or '0,"No error"'
                elif cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'): response = '0'
                else: response = '1'  # fixed canned response, not an instrument model
                try: os.write(self.master, (response + '\n').encode('ascii'))
                except OSError: return

    def close(self):
        self.stop.set()
        self.thread.join(.25)
        os.close(self.master)
        os.close(self._slave_guard)
