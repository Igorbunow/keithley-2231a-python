"""H05 power-sequence qualification using the TDS 3064B e*Scope interface.

@file h05_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Execute the fixed H05 power_only sequence and capture all three rails.
@details The operator gate is checked by qualification.py before any connection.
         The scope common-ground wiring is confirmed separately by the operator.
"""
from __future__ import annotations

import csv
from html.parser import HTMLParser
import hashlib
import math
from pathlib import Path
import re
import struct
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from common import StageAdapter, require_outputs

from keithley2231a import ChannelProfile, Keithley2231A, PowerStep, Profile, SafetyError

HOST = '192.168.22.105'
OFF = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
H05_TOKEN = ('CONFIRM H05 POWERED INDEPENDENT ALL_OFF LOAD=KEL103_USB=/dev/ttyACM0_115200 '
             'CH1=CR22 CH2=OPEN CH3=OPEN SCOPE=COMMON_NEGATIVE_X10 '
             'PSU=1.00V_0.100A DELAY=1S RECALL=OFF')
READS = frozenset(('*IDN?', 'ACQUIRE:STATE?', 'ACQUIRE:NUMACQ?', 'ACQUIRE:MODE?',
                   'ACQUIRE:STOPAFTER?', 'HORIZONTAL:RECORDLENGTH?',
                   'HORIZONTAL:MAIN:SCALE?', 'HORIZONTAL:TRIGGER:POSITION?',
                   'HORIZONTAL:DELAY:STATE?',
                   'TRIGGER:A:TYPE?', 'TRIGGER:A:MODE?', 'TRIGGER:A:EDGE:SOURCE?',
                   'TRIGGER:A:EDGE:SLOPE?', 'TRIGGER:A:EDGE:COUPLING?',
                   'TRIGGER:A:LEVEL?', 'ACQUIRE:NUMACQ?', '*ESR?', 'ALLEV?',
                   *[f'CH{ch}:PROBE?' for ch in (1, 2, 3)],
                   *[f'CH{ch}:SCALE?' for ch in (1, 2, 3)],
                   *[f'CH{ch}:{suffix}?' for ch in (1, 2, 3)
                     for suffix in ('IMPEDANCE', 'COUPLING', 'BANDWIDTH', 'INVERT',
                                    'DESKEW', 'OFFSET', 'POSITION')]))
WRITES = frozenset(('ACQUIRE:STATE STOP', 'ACQUIRE:STATE RUN',
                    'ACQUIRE:MODE SAMPLE', 'ACQUIRE:STOPAFTER SEQUENCE',
                    'HORIZONTAL:DELAY:STATE OFF', 'HORIZONTAL:RECORDLENGTH 10000',
                    'HORIZONTAL:MAIN:SCALE 2', 'HORIZONTAL:TRIGGER:POSITION 10',
                    'TRIGGER:A:TYPE EDGE', 'TRIGGER:A:MODE NORMAL',
                    'TRIGGER:A:EDGE:SOURCE CH1', 'TRIGGER:A:EDGE:SLOPE RISE',
                    'TRIGGER:A:EDGE:COUPLING DC', 'TRIGGER:A:LEVEL 0.5',
                    *[f'SELECT:CH{ch} ON' for ch in (1, 2, 3)], 'SELECT:CH4 OFF',
                    *[f'CH{ch}:{setting} {value}' for ch in (1, 2, 3)
                      for setting, value in (('IMPEDANCE', 'MEG'), ('COUPLING', 'DC'),
                                             ('BANDWIDTH', 'TWENTY'), ('INVERT', 'OFF'),
                                             ('DESKEW', '0'), ('OFFSET', '0'),
                                             ('POSITION', '0'), ('SCALE', '0.5'))]))


class _Response(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts = []
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'textarea' and dict(attrs).get('name') == 'name':
            self.active = True
            self.count += 1

    def handle_endtag(self, tag):
        if tag.lower() == 'textarea':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError('TDS e*Scope redirect refused')


class TDS3064B:
    """Small bounded e*Scope HTTP client; one request at a time, no retries."""
    def __init__(self, evidence_dir: Path, evidence):
        self.out, self.evidence = evidence_dir / 'scope', evidence
        self.out.mkdir(parents=True, exist_ok=False)
        self.sequence = 0
        self.base = f'http://{HOST}/'
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def post(self, endpoint, fields):
        if endpoint not in ('Comm.html', 'getsetup.set', 'getwfm.isf'):
            raise ValueError(f'TDS endpoint not allow-listed: {endpoint}')
        if any('\r' in value or '\n' in value for _, value in fields):
            raise ValueError('TDS form newline injection')
        payload = ''.join(f'{key}={value}\r\n' for key, value in fields).encode('ascii')
        request = Request(self.base + endpoint, data=payload,
                          headers={'Content-Type': 'text/plain'})
        error = None
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as http_error:
            error = http_error
            response = http_error
        with response:
            if response.geturl() != self.base + endpoint:
                raise OSError('TDS e*Scope redirected request')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise OSError('oversized TDS response')
        self.sequence += 1
        path = f'{self.sequence:04d}-{endpoint.replace(".", "-")}.raw'
        (self.out / path).write_bytes(raw)
        self.evidence.add('scope_http', endpoint=endpoint, fields=fields,
                          artifact=f'scope/{path}', bytes=len(raw),
                          sha256=hashlib.sha256(raw).hexdigest())
        if error is not None:
            raise error
        return raw

    def command(self, command):
        if '?' in command:
            if command not in READS:
                raise ValueError(f'TDS query not allowed: {command}')
        elif command not in WRITES:
            raise ValueError(f'TDS write not allowed: {command}')
        raw = self.post('Comm.html', [('COMMAND', command), ('gpibsend', 'Send')])
        parser = _Response()
        parser.feed(raw.decode('latin1'))
        if parser.count != 1:
            raise OSError('missing/ambiguous TDS response textarea')
        answer = ''.join(parser.parts).strip()
        self.evidence.add('scope_query' if '?' in command else 'scope_write',
                          command=command, response=answer)
        if '?' in command and not answer:
            raise OSError(f'empty TDS reply: {command}')
        return answer

    def number(self, command):
        value = float(self.command(command).split()[-1])
        if not math.isfinite(value):
            raise ValueError(f'non-finite TDS response to {command}')
        return value

    def events(self):
        """Reject scope command errors; tolerate only correlated power-on/OPC events."""
        esr = self.number('*ESR?')
        value = self.command('ALLEV?')
        fields = next(csv.reader([re.sub(r'^:?(?:ALLEV|ALLEVENTS)\s+', '', value, flags=re.I)]))
        codes = [field.strip() for field in fields[::2]]
        if esr != int(esr) or int(esr) & 0x3C or not codes:
            raise SafetyError(f'TDS event status requires review: ESR={esr}, events={value}')
        if codes == ['0']:
            return value
        benign = {'401': 0x80, '402': 0x01}
        if any(code not in benign or not int(esr) & benign[code] for code in codes):
            raise SafetyError(f'TDS event queue requires review: ESR={esr}, events={value}')
        return value

    def configure(self):
        identity = self.command('*IDN?')
        identity_fields = identity.split(',')
        if (len(identity_fields) < 4 or identity_fields[0].strip() != 'TEKTRONIX' or
                identity_fields[1].strip() != 'TDS 3064B'):
            raise SafetyError(f'unexpected TDS identity: {identity}')
        probes = {ch: self.number(f'CH{ch}:PROBE?') for ch in (1, 2, 3)}
        if probes != {1: 10.0, 2: 10.0, 3: 10.0}:
            raise SafetyError(f'TDS probe factors must remain x10: {probes}')
        if self.number('ACQUIRE:STATE?') != 0:
            raise SafetyError('TDS must be stopped before H05 setup')
        setup = self.post('getsetup.set', [('setupsend', 'Get')])
        if not setup or b'<HTML' in setup[:100].upper():
            raise OSError('TDS setup backup is empty or HTML')
        (self.out / 'original-setup.set').write_bytes(setup)
        self.command('ACQUIRE:STATE STOP')
        commands = ['ACQUIRE:MODE SAMPLE', 'ACQUIRE:STOPAFTER SEQUENCE',
                    'HORIZONTAL:DELAY:STATE OFF', 'HORIZONTAL:RECORDLENGTH 10000',
                    'HORIZONTAL:MAIN:SCALE 2', 'HORIZONTAL:TRIGGER:POSITION 10']
        for channel in (1, 2, 3):
            commands.extend((f'SELECT:CH{channel} ON',
                             f'CH{channel}:IMPEDANCE MEG', f'CH{channel}:COUPLING DC',
                             f'CH{channel}:BANDWIDTH TWENTY', f'CH{channel}:INVERT OFF',
                             f'CH{channel}:DESKEW 0', f'CH{channel}:OFFSET 0',
                             f'CH{channel}:POSITION 0', f'CH{channel}:SCALE 0.5'))
        commands += ['SELECT:CH4 OFF', 'TRIGGER:A:TYPE EDGE', 'TRIGGER:A:MODE NORMAL',
                     'TRIGGER:A:EDGE:SOURCE CH1', 'TRIGGER:A:EDGE:SLOPE RISE',
                     'TRIGGER:A:EDGE:COUPLING DC', 'TRIGGER:A:LEVEL 0.5']
        for command in commands:
            self.command(command)
        for query, expected in (('ACQUIRE:MODE?', 'SAMPLE'),
                                ('ACQUIRE:STOPAFTER?', 'SEQUENCE'),
                                ('TRIGGER:A:TYPE?', 'EDGE'),
                                ('TRIGGER:A:MODE?', 'NORMAL'),
                                ('TRIGGER:A:EDGE:SOURCE?', 'CH1'),
                                ('TRIGGER:A:EDGE:SLOPE?', 'RISE'),
                                ('TRIGGER:A:EDGE:COUPLING?', 'DC')):
            actual = self.command(query).split()[-1].upper()
            if actual != expected:
                raise SafetyError(f'TDS readback mismatch {query}: {actual}')
        if self.number('HORIZONTAL:RECORDLENGTH?') != 10000:
            raise SafetyError('TDS record length readback mismatch')
        if not math.isclose(self.number('HORIZONTAL:MAIN:SCALE?'), 2.0, abs_tol=1e-6):
            raise SafetyError('TDS horizontal scale readback mismatch')
        if not math.isclose(self.number('HORIZONTAL:TRIGGER:POSITION?'), 10.0, abs_tol=1e-6):
            raise SafetyError('TDS trigger position readback mismatch')
        if self.number('HORIZONTAL:DELAY:STATE?') != 0:
            raise SafetyError('TDS horizontal delay unexpectedly enabled')
        if not math.isclose(self.number('TRIGGER:A:LEVEL?'), 0.5, abs_tol=1e-6):
            raise SafetyError('TDS trigger level readback mismatch')
        scales = {ch: self.number(f'CH{ch}:SCALE?') for ch in (1, 2, 3)}
        if scales != {1: 0.5, 2: 0.5, 3: 0.5}:
            raise SafetyError(f'TDS channel scale readback mismatch: {scales}')
        probes = {ch: self.number(f'CH{ch}:PROBE?') for ch in (1, 2, 3)}
        if probes != {1: 10.0, 2: 10.0, 3: 10.0}:
            raise SafetyError(f'TDS probe factors changed during setup: {probes}')
        for channel in (1, 2, 3):
            for suffix, expected in (('IMPEDANCE', 'MEG'), ('COUPLING', 'DC'),
                                     ('BANDWIDTH', 'TWENTY')):
                actual = self.command(f'CH{channel}:{suffix}?').split()[-1].upper()
                if actual != expected:
                    raise SafetyError(f'TDS CH{channel} {suffix} readback mismatch: {actual}')
            if self.number(f'CH{channel}:INVERT?') != 0:
                raise SafetyError(f'TDS CH{channel} inversion is enabled')
            for suffix in ('DESKEW', 'OFFSET', 'POSITION'):
                if not math.isclose(self.number(f'CH{channel}:{suffix}?'), 0.0, abs_tol=1e-6):
                    raise SafetyError(f'TDS CH{channel} {suffix} readback mismatch')
        self.events()
        return {'identity': identity, 'probe_factors': probes}

    def arm(self):
        if self.number('ACQUIRE:STATE?') != 0:
            raise SafetyError('TDS unexpectedly running before arm')
        self.initial_acquisitions = int(self.number('ACQUIRE:NUMACQ?'))
        self.evidence.add('scope_acquisition_baseline', count=self.initial_acquisitions)
        self.command('ACQUIRE:STATE RUN')

    def collect(self, timeout_s=45):
        deadline = time.monotonic() + timeout_s
        while self.number('ACQUIRE:STATE?') != 0:
            if time.monotonic() >= deadline:
                raise TimeoutError('TDS H05 acquisition timeout; no forced trigger/rearm')
            time.sleep(0.2)
        count = int(self.number('ACQUIRE:NUMACQ?'))
        # NUMACQ counts acquisitions since acquisition started, not across STOP/RUN cycles.
        # H05 requests one single-sequence record containing all six channel edges.
        if count != 1:
            raise SafetyError(f'TDS capture count mismatch: {count}')
        self.events()
        all_rows = {}
        for channel in (1, 2, 3):
            raw = self.post('getwfm.isf', [('command', f'select:ch{channel} on'),
                                            ('command', 'save:waveform:fileformat internal'),
                                            ('wfmsend', 'Get')])
            (self.out / f'CH{channel}.isf').write_bytes(raw)
            rows = decode_isf(raw, channel)
            with (self.out / f'CH{channel}.csv').open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(('time_s', 'voltage_v'))
                writer.writerows(rows)
            all_rows[channel] = rows
        self.events()
        return all_rows


def decode_isf(raw: bytes, channel: int):
    """Decode a Tektronix definite-length Y waveform, retaining raw ISF too."""
    match = re.search(rb'(?:^|;)\s*:?(?:CURVE|CURV)\s+#([1-9])', raw, re.I)
    if not match:
        raise ValueError('TDS waveform lacks definite CURVE data block')
    header = raw[:match.start()].decode('ascii')
    fields = {}
    for part in re.split(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', header):
        item = part.strip().split(None, 1)
        if len(item) == 2:
            fields[item[0].split(':')[-1].upper()] = item[1].strip().strip('"')
    aliases = {'BYT_N': 'BYT_NR', 'BIT_N': 'BIT_NR', 'ENC': 'ENCDG',
               'BN_F': 'BN_FMT', 'BYT_O': 'BYT_OR', 'NR_P': 'NR_PT',
               'PT_F': 'PT_FMT', 'PT_O': 'PT_OFF', 'WFI': 'WFID',
               'XIN': 'XINCR', 'XZE': 'XZERO', 'XUN': 'XUNIT',
               'YMU': 'YMULT', 'YOF': 'YOFF', 'YZE': 'YZERO', 'YUN': 'YUNIT'}
    fields = {aliases.get(k, k): v for k, v in fields.items()}
    need = ('BYT_NR', 'BIT_NR', 'ENCDG', 'BN_FMT', 'BYT_OR', 'NR_PT', 'PT_FMT',
            'XINCR', 'XZERO', 'PT_OFF', 'YMULT', 'YOFF', 'YZERO', 'XUNIT', 'YUNIT')
    if any(key not in fields for key in need):
        raise ValueError('incomplete TDS waveform preamble')
    width, bits, count = (int(fields[key]) for key in ('BYT_NR', 'BIT_NR', 'NR_PT'))
    if width not in (1, 2) or not 1 <= bits <= width * 8 or not 1 <= count <= 10000:
        raise ValueError('unsupported TDS waveform geometry')
    if fields['PT_FMT'] != 'Y' or fields['BN_FMT'] not in ('RI', 'RP') or fields['BYT_OR'] not in ('MSB', 'LSB'):
        raise ValueError('unsupported TDS waveform encoding')
    if fields['XUNIT'].lower() != 's' or fields['YUNIT'].upper() != 'V':
        raise ValueError('unexpected TDS waveform units')
    pos = match.end()
    digits = int(match.group(1))
    size_text = raw[pos:pos + digits]
    if len(size_text) != digits or not size_text.isdigit():
        raise ValueError('invalid TDS binary block size')
    size = int(size_text)
    start = pos + digits
    payload = raw[start:start + size]
    if size != count * width or len(payload) != size or raw[start + size:].strip(b'\r\n'):
        raise ValueError('truncated or oversized TDS waveform')
    code = {('RI', 1): 'b', ('RP', 1): 'B', ('RI', 2): 'h', ('RP', 2): 'H'}[(fields['BN_FMT'], width)]
    samples = struct.unpack(('>' if fields['BYT_OR'] == 'MSB' else '<') + str(count) + code, payload)
    xinc, xzero, ptoff, ymult, yoff, yzero = (float(fields[key]) for key in
        ('XINCR', 'XZERO', 'PT_OFF', 'YMULT', 'YOFF', 'YZERO'))
    if not all(math.isfinite(value) for value in (xinc, xzero, ptoff, ymult, yoff, yzero)) or xinc <= 0 or ymult <= 0:
        raise ValueError('invalid TDS waveform scale')
    if fields.get('WFID', '').upper().split(',')[0] != f'CH{channel}':
        raise ValueError(f'TDS waveform CH{channel} identity mismatch: {fields.get("WFID")}')
    return [(xzero + xinc * (index - ptoff), yzero + ymult * (sample - yoff))
            for index, sample in enumerate(samples)]


def transitions(rows, threshold=0.5):
    """Return raw midpoint crossings, including chatter for diagnostic evidence."""
    edges = []
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if (v0 < threshold <= v1) or (v0 > threshold >= v1):
            fraction = (threshold - v0) / (v1 - v0)
            edges.append((t0 + fraction * (t1 - t0), 'RISE' if v1 > v0 else 'FALL'))
    return edges


def qualified_transitions(rows):
    """Find full-rail edges at 10% rise/90% fall, qualified by the opposite rail.

    Open outputs can discharge slowly and chatter around the midpoint. A single
    10% rising or 90% falling crossing identifies the start of the physical edge;
    requiring the opposite rail before accepting it rejects noise without moving
    the timestamp to the end of the transition.
    """
    if len(rows) < 2:
        raise ValueError('TDS waveform is too short for edge analysis')
    voltages = [voltage for _, voltage in rows]
    low = min(voltages)
    high = max(voltages)
    span = high - low
    if not math.isfinite(span) or span <= 0:
        raise ValueError('TDS waveform has no finite voltage span')
    rise_level = low + 0.1 * span
    fall_level = low + 0.9 * span
    if voltages[0] >= rise_level:
        raise SafetyError('H05 scope capture did not start with the output low')

    edges = []
    is_high = False
    index = 0
    while index < len(rows) - 1:
        found = False
        for crossing_index in range(index, len(rows) - 1):
            t0, v0 = rows[crossing_index]
            t1, v1 = rows[crossing_index + 1]
            if not is_high and v0 < rise_level <= v1:
                confirmation = None
                qualification = None
                for j in range(crossing_index + 1, len(rows)):
                    if rows[j][1] >= fall_level:
                        confirmation, qualification = 'HIGH', j
                        break
                    if rows[j][1] <= rise_level:
                        confirmation, qualification = 'LOW', j
                        break
                if confirmation != 'HIGH':
                    continue
                fraction = (rise_level - v0) / (v1 - v0)
                edges.append((t0 + fraction * (t1 - t0), 'RISE'))
                is_high = True
                index = qualification
                found = True
                break
            if is_high and v0 > fall_level >= v1:
                confirmation = None
                qualification = None
                for j in range(crossing_index + 1, len(rows)):
                    if rows[j][1] <= rise_level:
                        confirmation, qualification = 'LOW', j
                        break
                    if rows[j][1] >= fall_level:
                        confirmation, qualification = 'HIGH', j
                        break
                if confirmation != 'LOW':
                    continue
                fraction = (fall_level - v0) / (v1 - v0)
                edges.append((t0 + fraction * (t1 - t0), 'FALL'))
                is_high = False
                index = qualification
                found = True
                break
        if not found:
            break
    return edges


class H05Scope(StageAdapter):
    """Allow-list Keithley SCPI and record every reply/write in the evidence."""
    WRITES = frozenset(('SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:SER 0', 'OUTP:PARALLEL 0',
                        'OUTP:TRAC 0', 'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
                        'VOLT:LIM 1.00', 'VOLT:LIM:STAT 1', 'CURR 0.100',
                        'VOLT 1.00', 'CHAN:OUTP 0', 'CHAN:OUTP 1',
                        *[f'INST:NSEL {ch}' for ch in (1, 2, 3)]))
    READS = frozenset(('*IDN?', 'SYST:ERR?', 'INST:NSEL?', 'OUTP:SER?', 'OUTP:PARALLEL?',
                       'OUTP:TRAC?', 'VOLT:LIM?', 'VOLT:LIM:STAT?', 'VOLT?', 'CURR?',
                       'CHAN:OUTP?', 'MEAS:VOLT?', 'MEAS:CURR?', *OFF,
                       'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?'))
    MAX_COMMANDS = 2000
    SHUTDOWN_RESERVE = 64
    MAX_SECONDS = 300



def h05(transport, evidence, load, scope_host=HOST):
    """Run one fixed low-energy H05 sequence and capture all channel transitions."""
    if scope_host != HOST:
        raise ValueError('H05 scope host is fixed to the preflighted TDS 3064B')
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H05Scope(channel, evidence))
    io = dev.debug_transport
    scope = TDS3064B(evidence.path.parent, evidence)
    outcome = 'ABORTED'
    try:
        identity = dev.identity()
        evidence.add('psu_identity', command='*IDN?', response=identity)
        initial_error = dev.error()
        code, message = initial_error.split(',', 1)
        evidence.add('initial_error_queue', response=initial_error)
        if int(code.strip()) != 0 or not message.strip():
            raise SafetyError(f'initial PSU error queue is not clear: {initial_error}')
        states = dev.output_states()
        evidence.add('initial_output_states', channels=states)
        if any(states.values()):
            raise SafetyError(f'output initially ON: {states}')
        if load.identify().upper().find('KEL103') < 0:
            raise SafetyError('wrong electronic load identity')
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must start OFF')
        load.configure_h04p_cr(22.0)
        profile = Profile('H05 fixed three-channel sequence',
                          tuple(ChannelProfile(1.0, 0.100, 1.0, 0.100)
                                for _ in range(3)),
                          wiring='independent', lock_front_panel=False,
                          verify_every_s=3600.0, access='power_only',
                          power_sequence=(PowerStep(1, 0.0), PowerStep(2, 1.0),
                                          PowerStep(3, 1.0)))
        dev.apply_profile(profile)
        evidence.add('profile', name=profile.name, access='power_only', wiring='independent',
                     per_channel={'max_voltage_v': 1.0, 'max_current_a': 0.1,
                                  'initial_voltage_v': 1.0, 'initial_current_a': 0.1},
                     power_sequence=[{'channel': step.channel, 'delay_s': step.delay_s}
                                     for step in profile.power_sequence])
        for operation in (lambda: dev.set_channel(1, voltage=0.99),
                          lambda: dev.timer(1, 1.0),
                          lambda: dev.sequence([(0.0, 1, True)]),
                          lambda: dev.output(1, True)):
            before = io.commands
            try:
                operation()
            except SafetyError:
                pass
            else:
                raise SafetyError('power_only API accepted a forbidden call')
            if io.commands != before:
                raise SafetyError('power_only API rejection emitted SCPI')
        evidence.add('api_guards', set_channel='REJECTED_NO_SCPI',
                     timer='REJECTED_NO_SCPI', sequence='REJECTED_NO_SCPI',
                     direct_output='REJECTED_NO_SCPI', outcome='PASS')
        scope_configuration = scope.configure()
        evidence.add('scope_configuration', **scope_configuration)
        for channel in (1, 2, 3):
            reading = dev.read_channel(channel, with_regulation=False)
            if reading.enabled or abs(reading.voltage_set_v - 1.0) > .0049 or abs(reading.current_limit_set_a - .1) > .00049:
                raise SafetyError(f'CH{channel} initial readback not OFF at 1.00 V/0.100 A')
            evidence.add('pre_run_readback', channel=channel, enabled=reading.enabled,
                         voltage_set_v=reading.voltage_set_v, current_limit_set_a=reading.current_limit_set_a)
        scope.arm()
        evidence.add('scope_armed')
        load.set_input(True)
        evidence.add('kel103_input', state='ON')
        started = time.monotonic()
        dev.power_on()
        on_finished = time.monotonic()
        psu_readings = {}
        for channel in (1, 2, 3):
            reading = dev.read_channel(channel, with_regulation=False)
            if not reading.enabled:
                raise SafetyError(f'CH{channel} did not read ON after power_on()')
            psu_readings[channel] = {
                'voltage_v': reading.voltage_v, 'current_a': reading.current_a,
                'power_w': reading.power_w, 'enabled': reading.enabled,
                'voltage_set_v': reading.voltage_set_v,
                'current_limit_set_a': reading.current_limit_set_a}
        evidence.add('all_channels_on_readback', channels=psu_readings)
        load_measurement = load.measurements()
        evidence.add('kel103_all_channels_on_measurement', **load_measurement)
        dev.power_off()
        off_finished = time.monotonic()
        evidence.add('sequence_call_timing', power_on_duration_s=on_finished-started,
                     power_off_duration_s=off_finished-on_finished)
        load.set_input(False)
        evidence.add('kel103_input', state='OFF')
        waveforms = scope.collect()
        midpoint_crossings = {channel: transitions(rows) for channel, rows in waveforms.items()}
        per_channel = {channel: qualified_transitions(rows)
                       for channel, rows in waveforms.items()}
        time_axes = [tuple(t for t, _ in waveforms[channel]) for channel in (1, 2, 3)]
        if not time_axes[0] == time_axes[1] == time_axes[2]:
            raise SafetyError('TDS channel waveform time axes do not match')
        all_edges = sorted((time_s, channel, direction)
                           for channel, edges in per_channel.items()
                           for time_s, direction in edges)
        expected = [(1, 'RISE'), (2, 'RISE'), (3, 'RISE'),
                    (3, 'FALL'), (2, 'FALL'), (1, 'FALL')]
        actual = [(channel, direction) for _, channel, direction in all_edges]
        if actual != expected:
            raise SafetyError(f'H05 measured edge order mismatch: {actual}')
        evidence.add('scope_midpoint_crossings', threshold_v=0.5,
                     crossings=[{'time_s': t, 'channel': ch, 'direction': d}
                                for ch, crossings in midpoint_crossings.items()
                                for t, d in crossings],
                     outcome='DIAGNOSTIC_ONLY')
        intervals = [all_edges[index + 1][0] - all_edges[index][0] for index in range(5)]
        # Edge onset uses 10% rising / 90% falling crossings; the opposite rail
        # qualifies each full transition and midpoint chatter remains diagnostic.
        if any(interval < 0.95 for interval in (intervals[0], intervals[1], intervals[3], intervals[4])):
            raise SafetyError(f'H05 minimum transition delay failed: {intervals}')
        evidence.add('scope_edges', edges=[{'time_s': t, 'channel': ch, 'direction': d}
                                          for t, ch, d in all_edges],
                     measurement='10% rising / 90% falling edge onset; qualified by opposite rail',
                     intervals_s=intervals, minimum_delay_s=1.0,
                     measured_tolerance_s=0.05, outcome='PASS')
        states = dev.output_states()
        if any(states.values()):
            raise SafetyError(f'PSU output remains ON after power_off(): {states}')
        error = dev.error()
        if not error.startswith('0,'):
            raise SafetyError(f'PSU error queue not clear: {error}')
        evidence.add('final_status', output_masks=states, error_queue=error,
                     kel103_input=load.input_state())
        outcome = 'PASS'
    except BaseException as exc:
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc))
        print(f'H05 stopped: {type(exc).__name__}: {exc}')
        try:
            if load.input_state() != 'OFF': load.set_input(False)
        except BaseException: pass
        try:
            if io.live:
                io.cleanup = True
                dev.emergency_off()
                dev.diagnostic_write('SYST:LOC')
        except BaseException as off_error:
            evidence.add('shutdown_error', exception_type=type(off_error).__name__, message=str(off_error))
        try:
            if scope.number('ACQUIRE:STATE?') != 0:
                scope.command('ACQUIRE:STATE STOP')
        except BaseException as scope_error:
            evidence.add('scope_stop_error', exception_type=type(scope_error).__name__,
                         message=str(scope_error))
        outcome = 'ABORTED'
    finally:
        try:
            if outcome == 'PASS' and io.live: dev.close()
            else: io.close()
        except BaseException: outcome = 'ABORTED'
        try: load.close()
        except BaseException: outcome = 'ABORTED'
    evidence.add('stage_outcome', outcome=outcome)
    print(f'H05 {outcome}; physical OFF confirmation still required.')
    return outcome
