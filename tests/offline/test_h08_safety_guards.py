"""H08 fail-closed faults: no false PASS, independent PSU shutdown, verified timers."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from support.qualification_transport import FakeTransport
import h08_stage
from qualification import Evidence
from keithley2231a import SafetyError


class _Clock:
    now = 10.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


class _Source(FakeTransport):
    def __init__(self, clock, *, fail_psu_off=False, fail_on_query=False, stuck_timer=False, bad_timer_readback=False):
        super().__init__()
        self.clock = clock
        self.timer_start = None
        self.fail_psu_off = fail_psu_off
        self.fail_on_query = fail_on_query
        self.stuck_timer = stuck_timer
        self.bad_timer_readback = bad_timer_readback
        self.off_attempts = 0
        self.uncertain_failure_at = None

    def write(self, command):
        if command == 'OUTP 1': self.timer_start = self.clock.now
        if command == 'OUTP 0' and any(v['CHAN:OUTP'] for v in self.values.values()):
            self.off_attempts += 1
            if self.fail_psu_off: raise OSError('simulated failed PSU OFF')
        return super().write(command)

    def query(self, command):
        self.clock.now += 0.002
        if (self.fail_on_query and command == 'MEAS:VOLT?' and
                any(v['CHAN:OUTP'] for v in self.values.values())):
            self.uncertain_failure_at = len(self.cmds)
            raise OSError('simulated broken PSU UART')
        timers = [self.values[ch]['OUTP:TIM:DEL'] for ch in (1, 2)
                  if self.values[ch]['OUTP:TIM:STAT']]
        if timers and not self.stuck_timer and self.timer_start is not None:
            if self.clock.now - self.timer_start >= min(timers):
                for ch in (1, 2): self.values[ch]['CHAN:OUTP'] = 0.
        if command == 'OUTP:TIM:STAT?' and self.bad_timer_readback:
            self.cmds.append(command)
            return '1'
        if command == 'MEAS:CURR?':
            self.cmds.append(command)
            return '0'
        if command == 'MEAS:VOLT?' and not self.values[self.active_ch]['CHAN:OUTP']:
            self.cmds.append(command)
            return '0'
        return super().query(command)


class _Meter:
    channel = 1
    configured = False
    closed = False
    live = True

    def __init__(self, source, *, corrupt_live=None):
        self.source = source
        self.corrupt_live = corrupt_live
        self.measurements = 0

    def configure_voltage(self): self.configured = True

    def measure(self):
        self.measurements += 1
        settings = self.source.values[self.channel]
        voltage = settings['VOLT'] if settings['CHAN:OUTP'] else 0.
        if voltage and self.corrupt_live is not None:
            if isinstance(self.corrupt_live, BaseException):
                self.live = False
                raise self.corrupt_live
            return {'voltage_v': self.corrupt_live}
        return {'voltage_v': voltage}

    def close(self): self.closed = True


class H08FailClosedTests(unittest.TestCase):
    def _run(self, *, broken_evidence=None, meter_fault=None,
             fail_psu_off=False, fail_on_query=False, stuck_timer=False,
             bad_timer_readback=False):
        clock = _Clock()
        source = _Source(clock, fail_psu_off=fail_psu_off,
                         fail_on_query=fail_on_query, stuck_timer=stuck_timer,
                         bad_timer_readback=bad_timer_readback)
        meter = _Meter(source, corrupt_live=meter_fault)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Evidence(Path(tmp) / 'events.jsonl', 'H08')
            original_add = evidence.add
            broken = [False]

            def injecting_add(event, **fields):
                if (event == broken_evidence) or broken[0]:
                    broken[0] = True
                    raise OSError('simulated full evidence filesystem')
                return original_add(event, **fields)
            if broken_evidence: evidence.add = injecting_add

            def answer(prompt):
                token = next(s for s in prompt.splitlines() if s.startswith('CONFIRM '))
                if 'MOVE CH1->CH2' in token: meter.channel = 2
                return token
            with patch('builtins.input', side_effect=answer), \
                    patch('time.monotonic', clock.monotonic), \
                    patch('time.sleep', clock.sleep), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = h08_stage.h08(source, evidence, meter)
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            return result, source, meter, records

    def test_normal_tracking_still_passes_with_verified_final_off(self):
        result, psu, meter, events = self._run()
        self.assertEqual(result, 'PASS')
        self.assertTrue(psu.closed and meter.closed)
        self.assertTrue(all(not v['CHAN:OUTP'] for v in psu.values.values()))
        self.assertTrue(any(e['event'] == 'query_outcome' and e['outcome'] == 'PASS'
                            for e in events))

    def test_evidence_failure_while_powered_cannot_suppress_off(self):
        result, psu, meter, events = self._run(broken_evidence='tracking_measurement')
        self.assertEqual(result, 'ABORTED')
        self.assertTrue(all(not v['CHAN:OUTP'] for v in psu.values.values()))
        self.assertGreaterEqual(psu.off_attempts, 1)
        self.assertTrue(psu.closed and meter.closed)
        self.assertFalse(any(e['event'] == 'query_outcome' and e['outcome'] == 'PASS'
                             for e in events))

    def test_meter_io_failure_while_powered_still_offs_keithley(self):
        result, psu, meter, _ = self._run(meter_fault=OSError('DMM TCP broken'))
        self.assertEqual(result, 'ABORTED')
        self.assertTrue(all(not v['CHAN:OUTP'] for v in psu.values.values()))
        self.assertTrue(psu.closed and meter.closed)
        self.assertGreaterEqual(psu.off_attempts, 1)

    def test_psu_off_link_failure_never_retries_and_never_passes(self):
        result, psu, meter, _ = self._run(meter_fault=OSError('DMM TCP broken'),
                                          fail_psu_off=True)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(psu.off_attempts, 1)
        self.assertTrue(psu.closed and meter.closed)

    def test_uncertain_psu_uart_has_no_further_commands(self):
        result, psu, meter, _ = self._run(fail_on_query=True)
        self.assertEqual(result, 'ABORTED')
        self.assertIsNotNone(psu.uncertain_failure_at)
        self.assertEqual(len(psu.cmds), psu.uncertain_failure_at)
        self.assertTrue(psu.closed and meter.closed)

    def test_stuck_timer_and_meter_failure_downgrades_result(self):
        result, psu, meter, _ = self._run(stuck_timer=True)
        self.assertIn(result, ('FAIL', 'ABORTED'))
        self.assertTrue(all(not v['CHAN:OUTP'] for v in psu.values.values()))
        self.assertTrue(psu.closed and meter.closed)

    def test_timer_cleanup_requires_readback_and_never_claims_pass(self):
        result, psu, meter, _ = self._run(meter_fault=OSError('DMM TCP broken'),
                                          bad_timer_readback=True)
        self.assertEqual(result, 'ABORTED')
        self.assertTrue(all(not v['CHAN:OUTP'] for v in psu.values.values()))
        self.assertTrue(psu.closed and meter.closed)
        self.assertIn('OUTP:TIM:STAT?', psu.cmds)

    def test_final_evidence_failure_downgrades_pass(self):
        result, psu, meter, _ = self._run(broken_evidence='query_outcome')
        self.assertEqual(result, 'ABORTED')
        self.assertTrue(all(not v['CHAN:OUTP'] for v in psu.values.values()))
        self.assertTrue(psu.closed and meter.closed)

    def test_invalid_meter_types_are_rejected(self):
        for value in (float('nan'), float('inf'), True, '1.0'):
            with self.subTest(value=value):
                class Meter:
                    def measure(self): return {'voltage_v': value}
                with self.assertRaises(SafetyError):
                    h08_stage._meter_voltage(Meter())
