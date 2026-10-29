"""Real POSIX pseudo-terminal pair for testing the production SCPI path.

The peer implements a small, explicitly allowlisted response fixture and records
raw ASCII lines. Unknown commands are never silently acknowledged as success.
Tests exercise the production Keithley2231A dispatcher over a character stream.
No physical device is opened.
"""
from __future__ import annotations
import os
import select
import tty
import threading
import time


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
                 fault_code=None, off_by_channel=None, response_sequences=None,
                 fault_after=None):
        self.master, slave = os.openpty()
        tty.setraw(self.master)
        tty.setraw(slave)
        self.path = os.ttyname(slave)
        self._slave_guard = slave  # keep peer valid until test transport opens
        self.serial = serial
        self.rx = []
        self.rx_bytes = []
        self._rx_cond = threading.Condition()
        self.unsupported = []
        self.response_sequences = {cmd: list(values) for cmd, values in
                                   (response_sequences or {}).items()}
        if any(not values for values in self.response_sequences.values()):
            raise ValueError('virtual UART response sequences cannot be empty')
        self.fault_after = fault_after
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
                cmd = raw_line.rstrip(b'\r').decode('ascii')
                with self._rx_cond:
                    self.rx_bytes.append(bytes(raw_line) + b'\n')
                    self.rx.append(cmd)
                    self._rx_cond.notify_all()
                if cmd.startswith('INST:NSEL ') and cmd[-1:] in ('1', '2', '3'):
                    self.selected = int(cmd[-1])
                is_query = cmd.endswith('?')
                allowed_writes = {'SYST:REM', 'SYST:LOC', '*CLS', '*WAI', '*RST',
                                  'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'}
                allowed_reads = {'*IDN?', 'INST:NSEL?', 'CHAN:OUTP?',
                                 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
                                 'OUTP:TRAC?', '*ESE?', '*SRE?', '*PSC?', '*OPC?'}
                if cmd not in (allowed_reads if is_query else allowed_writes):
                    self.unsupported.append(cmd)
                    # An unknown write is rejected at the next error poll;
                    # an unknown query receives an unmistakably invalid reply.
                    if not is_query:
                        self.fault_code = '130,"Unsupported virtual UART command"'
                if cmd == self.fault_after:
                    self.fault_code = '120,"Injected SCPI error"'
                if not is_query or cmd == self.drop:
                    continue
                if cmd in self.response_sequences:
                    values = self.response_sequences[cmd]
                    response = values.pop(0) if len(values) > 1 else values[0]
                elif cmd in self.overrides:
                    response = self.overrides[cmd]
                elif cmd == '*IDN?':
                    response = f'Keithley instruments, 2231A-30-3, {self.serial}, 1.04-1.04'
                elif cmd == 'INST:NSEL?': response = str(self.selected)
                elif cmd == 'CHAN:OUTP?': response = str(self.off_by_channel.get(self.selected, 0))
                elif cmd == 'SYST:ERR?': response = self.fault_code or '0,"No error"'
                elif cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
                             '*ESE?', '*SRE?', '*PSC?'): response = '0'
                elif cmd == '*OPC?': response = '1'
                else: response = 'INVALID VIRTUAL UART QUERY'
                try: os.write(self.master, (response + '\n').encode('ascii'))
                except OSError: return

    def await_received(self, count, timeout=.4):
        """Synchronize with peer reader before asserting last unacknowledged write."""
        deadline = time.monotonic() + timeout
        with self._rx_cond:
            while len(self.rx) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._rx_cond.wait(remaining)
            return True

    def close(self):
        self.stop.set()
        self.thread.join(.25)
        os.close(self.master)
        os.close(self._slave_guard)
