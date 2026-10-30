"""External voltage-meter adapter for manual qualification, not the PSU protocol.

@file dmm7510.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Implement Meter.measure() with documented DMM7510 SCPI transactions.
@details Caller owns the explicit TCP connection and operator wiring gates.
"""
from __future__ import annotations

import math


class DMM7510:
    """Single-owner DC voltage adapter; no reconnect or command-language fallback.

    Reference: DMM7510-900-01D, section 5; DMM7510-901-01B, LAN interfaces.
    SCPI command set and front terminals must be selected by the operator.
    Transport uses raw TCP port 5025, LF termination and an explicit timeout.
    """
    def __init__(self, transport, evidence):
        self.transport, self.evidence = transport, evidence
        self.live, self.configured = True, False
        self.range_v = None
        self.range_a = None
        self.read_units = "V"
        self._scpi_ready = False

    def _check_error(self, context):
        error = self._transfer(':SYST:ERR?', query=True)
        code, message = error.split(',', 1)
        if int(code) != 0 or not message.strip():
            raise RuntimeError(f'DMM7510 error after {context}: {error}')

    def _transfer(self, command, *, query):
        if not self.live: raise RuntimeError('DMM7510 session failed; no retry')
        if not self._scpi_ready and command not in ('*IDN?', '*LANG?'):
            raise RuntimeError('Verify DMM7510 SCPI command set before sending SCPI')
        self.evidence.add('dmm7510_query' if query else 'dmm7510_write', command=command)
        try:
            answer = self.transport.query(command) if query else self.transport.write(command)
            if query:
                self.evidence.add('dmm7510_read', command=command, response=answer,
                                  units=self.read_units if command == ':READ?' else None)
            if self._scpi_ready and command != ':SYST:ERR?':
                self._check_error(command)
            return answer
        except BaseException:
            self.live, self.configured = False, False
            raise

    def identify(self):
        """Read-only identity and command-language probe, shared with setup."""
        identity = self._transfer('*IDN?', query=True)
        parts = [part.strip() for part in identity.split(',')]
        if (len(parts) != 4 or 'KEITHLEY' not in parts[0].upper()
                or parts[1].upper() not in ('MODEL DMM7510', 'DMM7510')):
            raise ValueError('Unexpected DMM7510 identity')
        language = self._transfer('*LANG?', query=True).strip().strip('"').upper()
        self.evidence.add('dmm7510_command_set', language=language)
        if language != 'SCPI':
            raise ValueError(f'DMM7510 command set is {language}; select SCPI and reboot')
        return identity

    def configure_voltage(self, range_v=10):
        """Identify before reset; select and verify a supported 10 V or 100 V range."""
        if type(range_v) is not int or range_v not in (10, 100):
            raise ValueError('DMM7510 DC voltage range must be 10 or 100 V')
        try:
            identity = self.identify()
            parts = [part.strip() for part in identity.split(',')]
            self._scpi_ready = True
            self._check_error('SCPI command-set verification')
            if self._transfer(':ROUT:TERM?', query=True).strip().upper() not in ('FRON', 'FRONT'):
                raise ValueError('Select DMM7510 front terminals before H08')
            for command in ('*RST', ':SENS:FUNC "VOLT:DC"',
                            f':SENS:VOLT:RANG {range_v}',
                            ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
                            ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF'):
                self._transfer(command, query=False)
            if (self._transfer(':SENS:FUNC?', query=True).strip().strip('"') != 'VOLT:DC'
                    or float(self._transfer(':SENS:VOLT:RANG?', query=True)) != range_v
                    or float(self._transfer(':SENS:VOLT:NPLC?', query=True)) != 1):
                raise ValueError('DMM7510 DC voltage configuration readback mismatch')
            self.evidence.add('dmm7510_configuration', firmware=parts[3],
                              function='DC voltage', range_v=range_v, nplc=1,
                              terminals='front', outcome='PASS')
            self.range_v, self.range_a, self.read_units = range_v, None, 'V'
            self.configured = True
        except BaseException:
            self.live, self.configured = False, False
            raise

    def configure_current(self, range_a=0.01):
        """Fixed FRONT 10-mA DCI range; AMPS + INPUT LO in series, PSU OFF.

        This does not check the physical jack or fuse; the operator must do so.
        Unlike DCV, the AMPS connection must NEVER be placed across a load.
        """
        if type(range_a) is not float or range_a != 0.01:
            raise ValueError('Only fixed DMM7510 FRONT 10-mA DCI range reviewed')
        try:
            identity = self.identify()
            self._scpi_ready = True
            self._check_error('SCPI command-set verification')
            if self._transfer(':ROUT:TERM?', query=True).strip().upper() not in ('FRON', 'FRONT'):
                raise ValueError('Select DMM7510 FRONT terminals for current')
            for command in ('*RST', ':SENS:FUNC "CURR"',
                            ':SENS:CURR:RANG:AUTO OFF', ':SENS:CURR:RANG 0.01',
                            ':SENS:CURR:NPLC 1', ':SENS:CURR:AZER ON',
                            ':SENS:CURR:AVER OFF', ':SENS:CURR:REL:STAT OFF'):
                self._transfer(command, query=False)
            function = self._transfer(':SENS:FUNC?', query=True).strip().strip('"').upper()
            r = float(self._transfer(':SENS:CURR:RANG?', query=True))
            nplc = float(self._transfer(':SENS:CURR:NPLC?', query=True))
            relative = self._transfer(':SENS:CURR:REL:STAT?', query=True).strip()
            autorange = self._transfer(':SENS:CURR:RANG:AUTO?', query=True).strip()
            if (function not in ('CURR', 'CURR:DC') or abs(r - range_a) > 1e-9
                    or abs(nplc - 1) > 1e-9 or relative != '0' or autorange != '0'):
                raise ValueError('DMM7510 fixed DCI configuration readback mismatch')
            self.evidence.add('dmm7510_configuration', firmware=identity.split(',')[-1].strip(),
                              function='DC current', range_a=range_a, nplc=1,
                              relative=False, autorange=False,
                              terminals='front', outcome='PASS',
                              physical_amps_jack='OPERATOR_CLAIM_ONLY')
            self.range_v, self.range_a, self.configured, self.read_units = None, range_a, True, 'A'
        except BaseException:
            self.live, self.configured = False, False
            raise

    def measure_current(self):
        """Fresh single DC current reading, no autorange, retries or fallback."""
        if not self.live or not self.configured or self.range_a != .01 or self.read_units != 'A':
            raise RuntimeError('DMM7510 fixed 10-mA DCI session is not ready')
        try:
            value = float(self._transfer(':READ?', query=True))
            if not math.isfinite(value) or abs(value) > .012:
                raise ValueError('DMM7510 current nonfinite/overrange; no retry')
            return {'current_a': value}
        except BaseException:
            self.live, self.configured = False, False
            raise

    def measure(self):
        """Trigger a fresh reading, rejecting nonfinite/overrange/multivalue replies."""
        if not self.live or not self.configured or self.read_units != 'V' or self.range_v is None:
            raise RuntimeError('DMM7510 voltage session is not ready')
        try:
            value = float(self._transfer(':READ?', query=True))
            if not math.isfinite(value) or self.range_v is None:
                raise ValueError('Invalid/nonfinite DMM7510 voltage')
            if abs(value) > self.range_v:
                # Preserve the actual observed voltage, unlike the old generic
                # error. Never auto-range or repeat a questionable measurement.
                raise ValueError(f'DMM7510 read {value:.9g} V outside configured '
                                 f'{self.range_v:g} V range')
            return {'voltage_v': value}
        except BaseException:
            self.live, self.configured = False, False
            raise

    def close(self):
        self.live, self.configured = False, False
        self.transport.close()
