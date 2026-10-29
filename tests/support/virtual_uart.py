"""Real POSIX pseudo-terminal pair for testing the production SCPI path.

The peer implements a small, explicitly allowlisted response fixture and records
raw ASCII lines. Unknown commands are never silently acknowledged as success.
Tests exercise the production Keithley2231A dispatcher over a character stream.
No physical device is opened.
"""
from __future__ import annotations
import os
import re
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
                 fault_after=None, profile_registers=False,
                 channel_response_sequences=None):
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
        # Explicit per-channel replies for measured digital readbacks. A sequence
        # is supplied by the test, not calculated from another channel's write:
        # coupling is firmware/hardware behavior, not part of this fixture.
        self.channel_response_sequences = {
            key: list(values) for key, values in (channel_response_sequences or {}).items()
        }
        if any(not values or not isinstance(key, tuple) or len(key) != 2 or
               key[0] not in (1, 2, 3) or key[1] != 'VOLT?'
               for key, values in self.channel_response_sequences.items()):
            raise ValueError('selected channel VOLT? sequences must be nonempty')
        if self.channel_response_sequences and not profile_registers:
            raise ValueError('channel readback script requires profile_registers')
        self.overrides = response_overrides or {}
        self.drop = drop
        self.fault_code = fault_code
        self.off_by_channel = off_by_channel or {}
        self.profile_registers = profile_registers
        self.ignore_shutdown = False
        self.ignore_global_off = False
        self.channel_registers = {ch: {'VOLT': '0.00', 'CURR': '0.000',
                                      'VOLT:LIM': '30.00' if ch < 3 else '5.00',
                                      'VOLT:LIM:STAT': '0', 'OUTP:TIM:STAT': '0'}
                                  for ch in (1, 2, 3)}
        # Three documented read-to-clear event latches; the peer is a wire
        # fixture, not a second implementation of the Keithley driver.
        self.event_latches = {'*ESR?': '7', 'STAT:QUES:EVEN?': '129',
                              'STAT:OPER:EVEN?': '255'}
        # Narrow digital SCPI readbacks; no physical tracking or combine-mode model.
        self.mode_registers = {'OUTP:SER': 0, 'OUTP:PARALLEL': 0, 'OUTP:TRAC': 0}
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
                # Optional narrow fixture for testing real apply_profile/output
                # SCPI over PTY.  Never enables output unless the driver writes it.
                if self.profile_registers:
                    reg_write = re.fullmatch(
                        r'(VOLT|CURR|VOLT:LIM|VOLT:LIM:STAT|OUTP:TIM:STAT|OUTP:TIM:DEL) '
                        r'([0-9]+(?:\.[0-9]+)?)', cmd)
                    if reg_write:
                        self.channel_registers[self.selected][reg_write[1]] = reg_write[2]
                    elif cmd in ('OUTP 0', 'OUTP 1'):
                        if not (cmd == 'OUTP 0' and (self.ignore_shutdown or self.ignore_global_off)):
                            for ch in (1, 2, 3):
                                self.off_by_channel[ch] = int(cmd[-1])
                    elif re.fullmatch(r'OUTP:(SER|PARALLEL|TRAC) [01]', cmd):
                        self.mode_registers[cmd.split()[0]] = int(cmd[-1])
                    elif cmd in ('CHAN:OUTP 0', 'CHAN:OUTP 1'):
                        if not (cmd == 'CHAN:OUTP 0' and self.ignore_shutdown):
                            self.off_by_channel[self.selected] = int(cmd[-1])
                is_query = cmd.endswith('?')
                allowed_writes = {'SYST:REM', 'SYST:LOC', '*CLS', '*WAI', '*RST',
                                  'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'}
                status_reads = {'SYST:MODU?', 'STAT:QUES:ENAB?', 'STAT:OPER:ENAB?'}
                for prefix in ('STAT:QUES:INST', 'STAT:OPER:INST'):
                    status_reads.add(f'{prefix}:ENAB?')
                    status_reads.update(f'{prefix}:ISUM{ch}:ENAB?' for ch in (1, 2, 3))
                allowed_reads = {'*IDN?', 'INST:NSEL?', 'CHAN:OUTP?',
                                 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
                                 'OUTP:TRAC?', '*ESE?', '*SRE?', '*PSC?', '*OPC?',
                                 '*ESR?', 'STAT:QUES:EVEN?', 'STAT:OPER:EVEN?'} | status_reads
                if self.profile_registers:
                    if is_query:
                        allowed_reads.update(
                            f'STAT:{kind}:INST:ISUM{ch}:COND?'
                            for kind in ('OPER', 'QUES') for ch in (1, 2, 3))
                        allowed_reads.update({'VOLT?', 'CURR?', 'VOLT:LIM?',
                                              'VOLT:LIM:STAT?', 'OUTP:TIM:STAT?',
                                              'OUTP:TIM:DEL?',
                                              'OUTP:STAT:ALL?', 'SYST:VERS?', '*STB?',
                                              'STAT:QUES:COND?', 'STAT:OPER:COND?',
                                              'MEAS:VOLT?', 'MEAS:CURR?'})
                    else:
                        allowed_writes.update({'SYST:RWLOCK', 'OUTP:SER 0',
                                               'OUTP:PARALLEL 0', 'OUTP:TRAC 0',
                                               'OUTP:SER 1', 'OUTP:PARALLEL 1',
                                               'OUTP:TRAC 1',
                                               'OUTP 0', 'OUTP 1',
                                               'CHAN:OUTP 0', 'CHAN:OUTP 1'})
                        if reg_write:
                            allowed_writes.add(cmd)
                if cmd not in (allowed_reads if is_query else allowed_writes):
                    self.unsupported.append(cmd)
                    # An unknown write is rejected at the next error poll;
                    # an unknown query receives an unmistakably invalid reply.
                    if not is_query:
                        self.fault_code = '130,"Unsupported virtual UART command"'
                if cmd == self.fault_after:
                    self.fault_code = '120,"Injected SCPI error"'
                # The event value is consumed as soon as the QUERY reaches the
                # peer, even when a response is dropped or malformed. A lost
                # event response must never cause the driver to query again.
                event_reply = None
                if cmd in self.event_latches:
                    event_reply = self.event_latches[cmd]
                    self.event_latches[cmd] = '0'
                if not is_query or cmd == self.drop:
                    continue
                if cmd in self.response_sequences:
                    values = self.response_sequences[cmd]
                    response = values.pop(0) if len(values) > 1 else values[0]
                elif cmd in self.overrides:
                    response = self.overrides[cmd]
                elif event_reply is not None:
                    response = event_reply
                elif cmd == '*IDN?':
                    response = f'Keithley instruments, 2231A-30-3, {self.serial}, 1.04-1.04'
                elif cmd == 'SYST:MODU?': response = '2231,1.04'
                elif cmd == 'STAT:QUES:ENAB?': response = '17'
                elif cmd == 'STAT:OPER:ENAB?': response = '255'
                elif cmd.startswith(('STAT:QUES:INST', 'STAT:OPER:INST')) and cmd in status_reads:
                    base = 100 if cmd.startswith('STAT:OPER:') else 0
                    channel = int(cmd.split(':ISUM')[-1].split(':')[0]) if ':ISUM' in cmd else 0
                    response = str(base + 11 + channel)
                elif self.profile_registers and (self.selected, cmd) in self.channel_response_sequences:
                    replies = self.channel_response_sequences[self.selected, cmd]
                    response = replies.pop(0) if len(replies) > 1 else replies[0]
                elif cmd == 'INST:NSEL?': response = str(self.selected)
                elif cmd == 'CHAN:OUTP?': response = str(self.off_by_channel.get(self.selected, 0))
                elif self.profile_registers and cmd == 'OUTP:STAT:ALL?':
                    response = str(int(any(self.off_by_channel.values())))
                elif self.profile_registers and cmd.endswith('?') and cmd[:-1] in self.channel_registers[self.selected]:
                    response = self.channel_registers[self.selected][cmd[:-1]]
                elif self.profile_registers and cmd == 'SYST:VERS?': response = '1999.0'
                elif self.profile_registers and cmd in ('*STB?', 'STAT:QUES:COND?', 'STAT:OPER:COND?'):
                    response = '0'
                elif self.profile_registers and cmd in ('MEAS:VOLT?', 'MEAS:CURR?'):
                    register = 'VOLT' if cmd == 'MEAS:VOLT?' else 'CURR'
                    response = self.channel_registers[self.selected][register]
                elif (self.profile_registers and
                      re.fullmatch(r'STAT:(OPER|QUES):INST:ISUM[123]:COND\?', cmd)):
                    ch = int(cmd[-7])
                    response = ('9' if self.off_by_channel.get(ch, 0) else '0') if cmd.startswith('STAT:OPER:') else '0'
                elif cmd == 'SYST:ERR?': response = self.fault_code or '0,"No error"'
                elif self.profile_registers and cmd[:-1] in self.mode_registers:
                    response = str(self.mode_registers[cmd[:-1]])
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

    def await_command(self, command, occurrences=1, timeout=.4):
        """Wait for an unacknowledged write to arrive on the peer thread."""
        with self._rx_cond:
            return self._rx_cond.wait_for(
                lambda: self.rx.count(command) >= occurrences, timeout=timeout)

    def close(self):
        self.stop.set()
        self.thread.join(.25)
        os.close(self.master)
        os.close(self._slave_guard)


def open_test_transport(peer, *, timeout=.17):
    """Use production SerialTransport when installed; otherwise thin POSIX PTY I/O."""
    try:
        import serial  # noqa: F401
    except ImportError:
        return PTYTransport(peer.path, timeout=timeout)
    from keithley2231a import SerialTransport
    return SerialTransport(peer.path, timeout=timeout)
