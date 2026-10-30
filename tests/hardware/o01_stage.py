"""O01 manually gated bounded PSU/DMM voltage pilot; one live CH1 output.

No simultaneous sampling, automatic connection retries or new raw SCPI path.
"""
from __future__ import annotations
from pathlib import Path
import sys

# Hardware qualification reuses example helpers, not driver orchestration.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from examples.iv_coordination import coordinated_voltage_sweep



import math
import re

from common import ShutdownEvidence, StageAdapter, operator_gate, require_outputs
from keithley2231a import (ChannelProfile, Keithley2231A, Profile, SafetyError)

VOLTAGES = (0.50, 1.00, 1.50)
CURRENT_LIMIT_A = 0.040
MAX_WINDOW_S = 5.0
MAX_START_LAG_S = 2.0
MAX_DELTA_V = 0.030
CONNECT_TOKEN = 'CONFIRM O01 CONNECT DMM7510 FRONT HI=CH1+ LO=CH1- PSU=ALL_OFF'
FINAL_TOKEN = 'CONFIRM O01 CH1=OFF CH2=OFF CH3=OFF DMM=DISCONNECTED LOAD=DISCONNECTED'


def approval_token(host=None):
    from bench_config import current_bench
    bench = current_bench()
    if host is None:
        host = bench.meter.host
    return (f'CONFIRM O01 INDEPENDENT ALL_OFF NO_LOAD NO_JUMPERS DMM7510='
            f'{host}:{bench.meter.network_port} FRONT=DCV10 NPLC=1 '
            'DMM_LEADS=DISCONNECTED RECALL=OFF CH1=V0.50_I0.040_VCAP2.00_ICAP0.100 '
            'CH2=V1.50_I0.200_VCAP3.00_ICAP0.200 '
            'CH3=V2.00_I0.300_VCAP4.00_ICAP0.300 '
            'SWEEP=0.50,1.00,1.50 MAX_WINDOW=5S MAX_DELTA=0.030V')


def preflight(port, host=None):
    from bench_config import current_bench
    bench = current_bench()
    if host is None:
        host = bench.meter.host
    print(f'[O01 STAGED / HW NOT_RUN] Keithley {port} 8N1 (baud из bench TOML); '
          f'DMM7510 {host}:{bench.meter.network_port} TCP SCPI FRONT DCV10 NPLC1.\n'
          'Вход: CH1/CH2/CH3 OFF, Independent; Output Recall=OFF, сетевой выключатель доступен. '
          'KEL103 INPUT OFF и ФИЗИЧЕСКИ ОТКЛЮЧЕНА, осциллограф отключён; нет DUT, перемычек, общей шины.\n'
          'До *RST DMM7510 оба щупа отключены; проверить SCPI/FRONT. '
          'DMM настраивается с отключёнными щупами. '
          'Профиль CH1 0.50 V/0.040 A cap 2.00 V/0.100 A; CH2 1.50 V/0.200 A '
          'cap 3.00 V/0.200 A; CH3 2.00 V/0.300 A cap 4.00 V/0.300 A.\n'
          'Только после отдельного CONNECT gate и подтверждения всех OFF: '
          'FRONT INPUT HI → CH1+, INPUT LO → CH1−, без current/SENSE разъёмов. '
          'Остальные выходы оставлены открытыми. '
          '0.50/1.00/1.50 V, лимит CH1 0.040 A, пауза 0.5 s, только CH1 ON.\n'
          'DMM читается ПОСЛЕ Keithley, не одновременно; max start lag 2 s, '
          'full host window 5 s, |ΔV|≤0.030 V; OFF перед CSV validation. '
          'Нет HW trigger, не определена инструментальная задержка преобразования.\n'
          'В случае ошибки: один OFF по известной живой связи, никакого повторного запроса '
          'после потери связи, ручная проверка OFF/отключения проводов.\n'
          f'Approval: {approval_token(host)}\nConnect: {CONNECT_TOKEN}\nFinal: {FINAL_TOKEN}\n'
          'R3.5.31: отдельный запуск требует --execute --allow-o01-on, интерактивный TTY, '
          'подтверждение стенда и два дополнительных операторских подтверждения.')


class O01Scope(StageAdapter):
    MAX_COMMANDS = 2400
    MAX_SECONDS = 120
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|VOLT\?|CURR\?|'
        r'VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP [01]|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|0\.00|0\.50|1\.00|1\.50|2\.00)|'
        r'CURR (?:0|0\.040|0\.200|0\.300)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if not query and command == 'CHAN:OUTP 1' and (
                self.selected != 1 or self.authorized_channel != 1 or
                any(self.outputs[ch] for ch in (2, 3)) or
                getattr(self, '_has_enabled_ch1', False)):
            raise SafetyError('O01 permits CH1 ON exactly once, after the connection gate')

    def _after_write(self, command):
        super()._after_write(command)
        if command == 'CHAN:OUTP 1':
            self._has_enabled_ch1 = True


def _check(cond, message):
    if not cond:
        raise SafetyError('O01: ' + message)


def o01(transport, evidence, meter):
    """Pilot runner; caller must own both links and secure all physical gates.

    Production entry point is the CLI with explicit --allow-o01-on.
    """
    sink = ShutdownEvidence(evidence)
    if getattr(meter, 'evidence', None) is evidence:
        meter.evidence = sink
    dev = Keithley2231A(
        transport, debug=True,
        debug_trace=lambda record: sink.add('driver_trace', trace=record),
        debug_adapter=lambda channel: O01Scope(channel, sink))
    io = dev.debug_transport
    status, detail = 'ABORTED', 'O01 incomplete'
    try:
        identity = dev.identity()
        _check('2231A-30-3' in identity, 'wrong PSU identity')
        baseline = dev.diagnostic_query('SYST:ERR?')
        _check(baseline.lstrip().startswith('0,'), 'preexisting PSU error queue')
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            _check(not dev._bool(dev.diagnostic_query(cmd)), 'Independent required')
        # DMM configuration may send *RST: do not connect FRONT leads until
        # this completes. Pre-run approval explicitly requires disconnection.
        meter.configure_voltage(10)
        _check(meter.live and meter.configured and meter.range_v == 10,
               'DMM configuration missing')
        sink.add('meter_prepared', function='DCV', range_v=10, nplc=1,
                 connection='FRONT LEADS DISCONNECTED during *RST')
        profile = Profile('O01 observer pilot no-load', (
            ChannelProfile(2.00, .100, .50, CURRENT_LIMIT_A),
            ChannelProfile(3.00, .200, 1.50, .200),
            ChannelProfile(4.00, .300, 2.00, .300)),
            wiring='independent', lock_front_panel=False, verify_every_s=5.0)
        dev.apply_profile(profile, confirmed_wiring='independent')
        dev.verify_profile()
        require_outputs(dev)
        sink.add('profile', channels=[{'max_v':p.max_voltage, 'max_a':p.max_current,
                                     'initial_v':p.initial_voltage,
                                     'initial_a':p.initial_current} for p in profile.channels])
        operator_gate(CONNECT_TOKEN,
                      'PSU CH1/CH2/CH3 OFF; DMM SCPI FRONT DCV10 verified. '
                      'Only now attach DMM FRONT HI to CH1+, LO to CH1−; '
                      'CH2/CH3, KEL103 and scope disconnected, no jumpers.', io)
        sink.add('operator_gate', token=CONNECT_TOKEN,
                 action='DMM front leads connected only with all PSU OFF')
        require_outputs(dev)
        off_voltage = meter.measure()['voltage_v']
        _check(type(off_voltage) in (int, float) and math.isfinite(off_voltage)
               and abs(off_voltage) <= .1, 'DMM saw unexpected voltage before ON')
        sink.add('dmm_off_check', voltage_v=off_voltage)
        io.authorized_channel = 1
        csv_path = evidence.path.parent / 'o01_sweep.csv'
        points = coordinated_voltage_sweep(
            dev, meter, 1, VOLTAGES, current_limit_a=CURRENT_LIMIT_A,
            settle_s=.5, csv_path=csv_path,
            max_start_lag_s=MAX_START_LAG_S, max_window_s=MAX_WINDOW_S,
            max_voltage_difference_v=MAX_DELTA_V)
        _check(len(points) == len(VOLTAGES), 'missing sweep points')
        for expected, point in zip(VOLTAGES, points):
            _check(point.requested_v == expected and point.regulation == 'CV',
                   'unexpected sweep setpoint or regulation')
            _check(abs(point.measured_v - expected) <= MAX_DELTA_V,
                   'Keithley voltage disagrees with setpoint')
            _check(0 <= point.measured_a <= .030 and 0 <= point.measured_w <= .050,
                   'unexpected current/power on high impedance DMM input')
            _check(point.external['source_dmm_window_s'] <= MAX_WINDOW_S and
                   abs(point.external['psu_dmm_delta_v']) <= MAX_DELTA_V,
                   'timing/voltage limit')
            sink.add('coordinated_measurement', index=point.index,
                     requested_v=point.requested_v, sample_timestamp_utc=point.timestamp_utc,
                     sample_monotonic_s=point.monotonic_s, measured_v=point.measured_v,
                     measured_a=point.measured_a, measured_w=point.measured_w,
                     regulation=point.regulation, external=point.external)
        sink.add('csv_verified', path=str(csv_path), rows=len(points),
                 columns=15, state='VERIFIED_AFTER_OFF')
        require_outputs(dev)
        status, detail = 'PASS', '3 sequential PSU/DMM readings, CSV and electrical OFF verified'
    except BaseException as exc:
        status = ('ABORTED' if not io.live or not getattr(meter, 'live', True) or
                  isinstance(exc, (OSError, TimeoutError, KeyboardInterrupt,
                                   InterruptedError, EOFError)) else 'FAIL')
        detail = f'O01 {type(exc).__name__}: {exc}; verify physical OFF'
        try:
            sink.add('stage_error', exception_type=type(exc).__name__,
                     detail=str(exc), psu_live=io.live,
                     meter_live=getattr(meter, 'live', None))
        except BaseException:
            status = 'ABORTED'
    finally:
        sink.cleanup = True
        io.cleanup = True
        shutdown_failure = None
        if io.live:
            try:
                dev.emergency_off()
                require_outputs(dev)
                sink.add('electrical_shutdown', state='VERIFIED_OFF',
                         devices=['Keithley'], dmm_is_passive=True)
            except BaseException as exc:
                shutdown_failure = f'PSU OFF unconfirmed: {type(exc).__name__}'
        else:
            shutdown_failure = 'PSU link uncertain; manual OFF required'
        if shutdown_failure:
            status = 'ABORTED'
            detail += '; ' + shutdown_failure
            try:
                sink.add('electrical_shutdown', state='UNKNOWN',
                         reason=shutdown_failure)
            except BaseException:
                pass
        if io.live and not shutdown_failure:
            try:
                dev.diagnostic_write('SYST:LOC')
            except BaseException as exc:
                status = 'ABORTED'
                detail += f'; local restore failed: {type(exc).__name__}'
        for device in (meter, transport):
            try:
                device.close()
            except BaseException as exc:
                status = 'ABORTED'
                detail += f'; close failure: {type(exc).__name__}'
        if sink.lost:
            status = 'ABORTED'
            detail += '; incomplete evidence'
        try:
            sink.add('query_outcome', outcome=status, stop_reason=detail,
                     verification='HW OBSERVED; manual review required, not HW VERIFIED')
        except BaseException:
            status = 'ABORTED'
    return status
