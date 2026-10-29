"""Simulated exact CH1-only CV/CC wire trace; no equipment I/O."""
from __future__ import annotations

import contextlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench_config import H03SingleCVCC
from qualification import Evidence
from support.qualification_transport import FakeTransport
import single_cvcc_stage as stage


POINT = H03SingleCVCC(
    channel=1, initial_voltage_v=(1., 1.5, 2.),
    initial_current_a=(.1, .2, .3), voltage_cap_v=(4., 4.05, 4.1),
    current_cap_a=(.3, .4, .5), cv_voltage_v=4.,
    cv_current_limit_a=.3, cv_resistance_ohm=40., cc_voltage_v=4.,
    cc_current_limit_a=.25, cc_resistance_ohm=12., max_power_w=1.2)


class Source(FakeTransport):
    def __init__(self):
        super().__init__()
        self.load = None

    def point(self):
        if self.load is None or self.load.state != 'ON' or not self.values[1]['CHAN:OUTP']:
            return 0., 0.
        v = min(self.values[1]['VOLT'], self.values[1]['CURR'] * self.load.resistance)
        return v, v / self.load.resistance

    def query(self, command):
        if command == 'MEAS:VOLT?':
            self.cmds.append(command)
            return str(self.point()[0])
        if command == 'MEAS:CURR?':
            self.cmds.append(command)
            if self.failed_query == command: raise OSError('simulated link failure')
            return str(self.point()[1])
        if command == 'STAT:OPER:INST:ISUM1:COND?':
            self.cmds.append(command)
            if not self.values[1]['CHAN:OUTP']:
                return '0'
            v, _ = self.point()
            return '9' if v + .001 >= self.values[1]['VOLT'] else '10'
        return super().query(command)


class Load:
    transport = 'USB serial (simulated)'
    port = 'MOCK'
    live = True

    def __init__(self, source):
        self.source, self.state, self.resistance = source, 'OFF', None
        self.actions, self.closed = [], False
        source.load = self

    def identify(self):
        self.actions.append('IDN')
        return 'KEL103 MOCK'

    def input_state(self):
        self.actions.append('INP?')
        return self.state

    def configure_cr(self, value):
        assert self.state == 'OFF'
        self.actions.append(f'CR {value:g}')
        self.resistance = value

    def set_input(self, state):
        self.actions.append('INP ON' if state else 'INP OFF')
        self.state = 'ON' if state else 'OFF'

    def measurements(self):
        self.actions.append('MEASURE')
        v, i = self.source.point()
        return {'kel103_voltage_v': v, 'kel103_current_a': i, 'kel103_power_w': v*i}

    def close(self):
        self.closed = True


class SingleRunnerTests(unittest.TestCase):
    def run_stage(self, source=None, load=None, gate=None):
        source = source or Source()
        load = load or Load(source)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Evidence(Path(tmp) / 'single.jsonl', 'H03_SINGLE_CVCC')
            if gate is None:
                def gate(token, prompt, adapter, **kwargs):
                    pass
            with patch.object(stage, 'operator_gate', side_effect=gate), \
                 contextlib.redirect_stdout(StringIO()):
                status = stage.run_single_cvcc(source, evidence, load, POINT)
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
        return status, source, load, records

    def test_two_points_ch1_only_complete_and_every_on_is_gated(self):
        observed = []
        def gate(token, prompt, adapter, **kwargs): observed.append(token)
        status, psu, load, records = self.run_stage(gate=gate)
        self.assertEqual(status, 'PASS', [r for r in records if r['event']=='stage_error'])
        self.assertEqual(len([r for r in records if r['event']=='single_cvcc_measurement']), 2)
        self.assertEqual([r['mode'] for r in records if r['event']=='single_cvcc_measurement'], ['CV', 'CC'])
        self.assertEqual(sum(c == 'CHAN:OUTP 1' for c in psu.cmds), 2)
        self.assertNotIn('OUTP 1', psu.cmds)
        self.assertNotIn('CHAN:OUTP 1', [c for c in psu.cmds if c.startswith('OUTP')])
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(psu.closed and load.closed)
        self.assertTrue(all(psu.values[ch]['CHAN:OUTP'] == 0 for ch in (1,2,3)))
        self.assertEqual(len(observed), 8)  # connect, enable, display, off per point
        self.assertFalse(any(a == b == 'SYST:ERR?' for a,b in zip(psu.cmds,psu.cmds[1:])))

    def test_output_on_is_rejected_before_wire_without_gate_or_for_other_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            psu = Source()
            evidence = Evidence(Path(tmp) / 'scope.jsonl', 'H03_SINGLE_CVCC')
            scope = stage.SingleCVCCScope(psu, evidence, POINT)
            scope.selected = 1
            with self.assertRaises(Exception): scope.write('CHAN:OUTP 1')
            self.assertEqual(psu.cmds, [])
            scope.selected, scope.authorized_channel, scope.enabled_by_gate = 2, 2, True
            with self.assertRaises(Exception): scope.write('CHAN:OUTP 1')
            self.assertEqual(psu.cmds, [])
            with self.assertRaises(Exception): scope.write('CURR 0.250')
            self.assertEqual(psu.cmds, [])
            with self.assertRaises(Exception): scope.write('OUTP 1')
            self.assertEqual(psu.cmds, [])

    def test_initial_on_blocks_before_profile_or_enable(self):
        psu = Source()
        psu.values[2]['CHAN:OUTP'] = 1
        status, psu, load, records = self.run_stage(source=psu)
        self.assertIn(status, ('FAIL','ABORTED'))
        self.assertNotIn('CURR 0', psu.cmds)
        self.assertNotIn('OUTP 0', psu.cmds)
        self.assertNotIn('CHAN:OUTP 1', psu.cmds)
        self.assertTrue(any(r['event']=='stage_error' for r in records))

    def test_link_loss_forbids_retries_and_new_commands(self):
        psu = Source()
        psu.failed_query = 'MEAS:CURR?'
        status, psu, load, records = self.run_stage(source=psu)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(psu.cmds[-1], 'MEAS:CURR?')
        self.assertNotIn('SYST:LOC', psu.cmds[-3:])
        self.assertTrue(any(r['event']=='query_outcome' and
                            r['verification']=='HW UNVERIFIED' for r in records))

    def test_load_input_initial_on_refuses_all_psu_commands_and_mutations(self):
        psu = Source()
        load = Load(psu)
        load.state = 'ON'
        status, psu, load, records = self.run_stage(source=psu, load=load)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(psu.cmds, [])
        self.assertNotIn('INP OFF', load.actions)
        self.assertTrue(any(r['event']=='stage_error' for r in records))

    def test_lost_load_link_uses_only_live_psu_shutdown(self):
        class UncertainLoad(Load):
            def measurements(self):
                self.live = False
                raise OSError('simulated KEL USB uncertainty')
        psu = Source()
        load = UncertainLoad(psu)
        status, psu, load, records = self.run_stage(source=psu, load=load)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(psu.values[1]['CHAN:OUTP'], 0)
        self.assertIn('OUTP 0', psu.cmds)
        self.assertNotIn('INP OFF', load.actions)
        self.assertTrue(any(r['event']=='query_outcome' and
                            r['verification']=='HW UNVERIFIED' for r in records))

    def test_korad_overpower_not_mislabeled_as_pass(self):
        class OverpowerLoad(Load):
            def measurements(self):
                sample = super().measurements()
                sample['kel103_power_w'] = 1.5
                return sample
        psu = Source()
        status, psu, load, records = self.run_stage(source=psu, load=OverpowerLoad(psu))
        self.assertNotEqual(status, 'PASS')
        self.assertEqual(psu.values[1]['CHAN:OUTP'], 0)
        self.assertEqual(load.state, 'OFF')
        self.assertFalse(any(r['event']=='single_cvcc_measurement' for r in records))

    def test_operator_gate_interruption_before_enable_never_issues_output_on(self):
        from keithley2231a import SafetyError
        def gate(token, prompt, adapter, **kwargs):
            if 'ENABLE' in token: raise InterruptedError('operator declined enable')
        status, psu, load, records = self.run_stage(gate=gate)
        self.assertNotEqual(status, 'PASS')
        self.assertNotIn('CHAN:OUTP 1', psu.cmds)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(any(r['event']=='stage_error' for r in records))

    def test_wrong_mode_fails_even_if_measurement_consistent(self):
        class WrongModeSource(Source):
            def query(self, cmd):
                if cmd == 'STAT:OPER:INST:ISUM1:COND?' and self.values[1]['CHAN:OUTP']:
                    self.cmds.append(cmd)
                    return '9'  # force CV even at CC point
                return super().query(cmd)
        psu = WrongModeSource()
        status, psu, load, records = self.run_stage(source=psu)
        self.assertNotEqual(status, 'PASS')
        self.assertEqual(load.state, 'OFF')
        self.assertEqual(sum(r['event']=='single_cvcc_measurement' for r in records), 1)


if __name__ == '__main__':
    unittest.main()
