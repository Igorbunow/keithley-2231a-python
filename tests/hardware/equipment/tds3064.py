"""Tektronix TDS3064B e*Scope qualification-only adapter.

No network access at import time. Explicit host and timeout from BenchConfig.
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

from keithley2231a import SafetyError

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
    def __init__(self, evidence_dir: Path, evidence, *, host: str, port: int, timeout_s: float = 10.0):
        self.out, self.evidence = evidence_dir / 'scope', evidence
        self.out.mkdir(parents=True, exist_ok=False)
        self.sequence = 0
        self.base = f'http://{host}:{port}/'
        self.timeout_s = timeout_s
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
            response = self.opener.open(request, timeout=self.timeout_s)
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

