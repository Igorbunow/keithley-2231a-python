"""H04P offline-only numeric fault injection and SCPI evidence tamper tests."""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hardware'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import powered_profile_stage as h04p
from audit_hw_scpi import audit_h04p_records
from tests.offline.test_h04p_shutdown import PointBench


def make_synthetic_h04p(*, initial_selected=None, verify_first_select=None):
    records = []
    def add(event, **kwargs):
        records.append({'test_id':'H04P', 'event':event, **kwargs})
    command_count = 0
    selected = 1
    def raw_reply(command, response):
        nonlocal command_count
        command_count += 1
        add('driver_trace', trace={'event':'query', 'command':command})
        add('query', command=command)
        add('driver_trace', trace={'event':'reply','command':command,'response':response})
        add('read', command=command, response=response)
    def wire(command, *, answer=None, error='0, "No error"'):
        nonlocal command_count, selected
        if command.endswith('?'):
            raw_reply(command, answer if answer is not None else '0')
        else:
            command_count += 1
            add('driver_trace',trace={'event':'write','command':command})
            add('write',command=command)
            if command.startswith('INST:NSEL '): selected = int(command[-1])
        if command not in ('SYST:LOC', 'SYST:ERR?'):
            raw_reply('SYST:ERR?', error)
    def select(ch):
        wire(f'INST:NSEL {ch}')
    add('bench_connection_confirmation')
    add('approval', variant='full')
    if initial_selected is not None:
        # Real instruments may retain CH2/CH3 selection between sessions.
        wire('INST:NSEL?', answer=str(initial_selected))
    for ch in (1,2,3):
        select(ch)
        if ch == 1 and verify_first_select is not None:
            wire('INST:NSEL?', answer=str(verify_first_select))
        wire('VOLT 4.51', error='120, "Parameter overflowed"')
        add('expected_instrument_rejection',command='VOLT 4.51',response='120, "Parameter overflowed"',outcome='EXPECTED_REJECTION')
        add('max_voltage_panel_limit',channel=ch)
        add('max_voltage_panel_readback',channel=ch)
        add('profile',channel=ch)
    add('operator_gate',token='CONFIRM H04P MAXV')
    expected={1:2.2, 2:3.3, 3:4.4}
    for ch in (1,2,3):
        if ch==1: add('operator_gate',token='CONFIRM H04P CONNECT CH1')
        else: add('operator_gate',token=f'CONFIRM H04P MOVE CH{ch-1}->CH{ch}')
        settings=[('control','common_3.30V','CV',3.3,.1,47),
                  ('control','channel_value','CV',expected[ch],.1,47),
                  ('control','profile_voltage_boundary','CV',4.5,.1,56),
                  ('control','current_limit_0.100A','CC',3.0,.1,22),
                  ('control','current_limit_0.250A','CC',3.0,.25,11),
                  ('control','current_limit_0.500A','CC',3.0,.5,5),
                  ('power_only','fixed_channel_preset','CV',expected[ch],.1,47)]
        for phase,point,mode,sv,si,res in settings:
            add('kel103_write',command=':FUNC CR')
            add('kel103_write',command=f':RES {res}OHM')
            add('kel103_write',command=':INP ON')
            select(ch)
            wire('CHAN:OUTP 1')
            for c in (1,2,3):
                select(c)
                wire('CHAN:OUTP?', answer='1' if c==ch else '0')
            vi=sv if mode=='CV' else si*res
            ii=vi/res if mode=='CV' else si
            pp=vi*ii
            add('measurement',channel=ch,phase=phase,point=point,expected_mode=mode,
                actual_mode=mode,set_voltage_v=sv,set_current_limit_a=si,
                load_resistance_ohm=float(res),keithley_voltage_v=vi,
                keithley_current_a=ii,keithley_power_api_w=pp,keithley_power_scpi_w=pp,
                kel103_voltage_v=vi,kel103_current_a=ii,kel103_power_w=pp,
                power_difference_max_w=.025,
                keithley_operation_status=(8 if ch==3 else 9 if mode=='CV' else 10),
                keithley_questionable_status=(1 if mode=='CV' else 2))
            select(ch)
            wire('CHAN:OUTP 0')
            for c in (1,2,3):
                select(c)
                wire('CHAN:OUTP?', answer='0')
            add('kel103_write',command=':INP OFF')
    add('operator_gate',token='CONFIRM H04P DISCONNECT CH3')
    wire('SYST:LOC')
    add('query_outcome',outcome='PASS',command_count=command_count)
    add('outcome',outcome='PASS',physical_off_confirmed=True)
    return records


class H04PNumericTests(unittest.TestCase):
    def setUp(self):
        self.b = PointBench()
        self.addCleanup(self.b.release)

    def measure_quiet(self):
        with redirect_stdout(io.StringIO()):
            self.b.measure()

    def test_historical_realistic_power_difference_under_25_mw_passes(self):
        self.b.dev.diagnostic_query.return_value='0.350'  # PSU: U*I=0.33
        self.b.load.measurements.return_value={'kel103_voltage_v':3.3,'kel103_current_a':.1,'kel103_power_w':.349}
        self.measure_quiet()

    def test_cross_instrument_power_mismatch_trips_even_when_each_is_self_consistent(self):
        # V/I tolerances alone would permit this discrepancy; P must be checked.
        self.b.load.measurements.return_value={'kel103_voltage_v':3.35,'kel103_current_a':.109,
                                               'kel103_power_w':3.35*.109}
        with self.assertRaisesRegex(Exception,'power consistency'):
            self.measure_quiet()
        self.assertEqual(self.b.input_state,'OFF')
        self.assertFalse(any(self.b.io.outputs.values()))

    def test_nonfinite_kel_power_rejected_and_both_off(self):
        self.b.load.measurements.return_value={'kel103_voltage_v':3.3,'kel103_current_a':.1,'kel103_power_w':float('nan')}
        with self.assertRaisesRegex(Exception,'non-finite'):
            self.measure_quiet()
        self.assertEqual(self.b.events[-2:],['psu.off','kel.off'])

    def test_nonfinite_psu_scpi_power_rejected_and_both_off(self):
        self.b.dev.diagnostic_query.return_value='NaN'
        with self.assertRaisesRegex(Exception,'non-finite'):
            self.measure_quiet()
        self.assertEqual(self.b.events[-2:],['psu.off','kel.off'])

    def test_infinite_api_measurement_rejected(self):
        self.b.dev.read_channel.return_value.voltage_v=float('inf')
        with self.assertRaisesRegex(Exception,'non-finite'):
            self.measure_quiet()
        self.assertEqual(self.b.events[-2:],['psu.off','kel.off'])

    def test_nonfinite_resistance_prevents_load_on(self):
        self.b.load.configure_cr.return_value=float('nan')
        with self.assertRaisesRegex(Exception,'non-finite'):
            self.measure_quiet()
        self.assertNotIn('kel.on',self.b.events)
        self.assertNotIn('psu.on',self.b.events)

    def test_nonfinite_panel_readback_rejected(self):
        class Dummy:
            def verify_profile(self): pass
            def diagnostic_write(self, command):pass
            def diagnostic_query(self, command):return 'nan' if command=='VOLT:LIM?' else '0'
        with self.assertRaisesRegex(Exception,'non-finite'):
            h04p._max_voltage_panel_gate(None,Dummy(),None)

    def test_nonfinite_remote_probe_readback_rejected_before_write(self):
        class Dummy:
            def __init__(self):self.queries=[];self.debug_bypass_profile=False
            def diagnostic_write(self, command):
                if command != 'INST:NSEL 1': raise AssertionError('unsafe write attempted')
            def diagnostic_query(self, command):
                self.queries.append(command)
                return 'NaN' if command=='VOLT:LIM?' else '1' if command=='VOLT:LIM:STAT?' else '0'
            def _bool(self, value):return value == '1'
        dev=Dummy()
        with patch.object(h04p,'require_outputs'):
            with self.assertRaisesRegex(Exception,'non-finite'):
                h04p._remote_max_voltage_scpi_probe(dev,None,None)
        self.assertEqual(dev.queries,['VOLT:LIM:STAT?','VOLT:LIM?'])


class H04PTraceTests(unittest.TestCase):
    def setUp(self):self.records=make_synthetic_h04p()
    def audit(self):return audit_h04p_records(self.records)
    def test_clean_synthetic_trace_passes(self):
        result=self.audit()
        self.assertEqual(result['status'],'PASS',result['issues'])
        self.assertEqual(result['observed_error_codes'],[120]*3)
    def test_initial_ch3_selection_is_accepted_before_first_write(self):
        # Regression from 2026-10-28T132642Z: initial INST:NSEL? = 3
        # is entirely valid before the first explicit INST:NSEL write.
        result = audit_h04p_records(make_synthetic_h04p(initial_selected=3))
        self.assertEqual(result['status'], 'PASS', result['issues'])

    def test_initial_channel_readback_outside_1_to_3_fails(self):
        result = audit_h04p_records(make_synthetic_h04p(initial_selected=4))
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any('Invalid selected-channel' in issue for issue in result['issues']))

    def test_explicit_selection_disagreement_still_fails(self):
        result = audit_h04p_records(make_synthetic_h04p(initial_selected=3, verify_first_select=3))
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any('disagrees with selected channel' in issue for issue in result['issues']))

    def test_correct_selection_acknowledgement_passes(self):
        result = audit_h04p_records(make_synthetic_h04p(initial_selected=3, verify_first_select=1))
        self.assertEqual(result['status'], 'PASS', result['issues'])

    def test_legacy_power_tolerance_gets_review_not_silent_pass(self):
        next(r for r in self.records if r['event']=='measurement')['power_difference_max_w']=.31
        self.assertEqual(self.audit()['status'],'PASS_WITH_REVIEW_NOTES')
    def test_nan_in_saved_measurement_never_passes(self):
        next(r for r in self.records if r['event']=='measurement')['kel103_power_w']=float('nan')
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_large_own_consistent_but_cross_inconsistent_power_fails(self):
        r=next(r for r in self.records if r['event']=='measurement')
        r['kel103_voltage_v']+=.055
        r['kel103_current_a']+=.008
        r['kel103_power_w']=r['kel103_voltage_v']*r['kel103_current_a']
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_missing_load_off_is_rejected(self):
        self.records.remove(next(r for r in self.records if r.get('event')=='kel103_write' and r.get('command')==':INP OFF'))
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_missing_psu_error_poll_is_rejected(self):
        a=next(r for r in self.records if r.get('event')=='driver_trace' and r['trace'].get('event')=='query' and r['trace']['command']=='SYST:ERR?')
        self.records.remove(a)
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_output_readback_wrong_neighbor_is_rejected(self):
        idx=next(i for i,r in enumerate(self.records) if r['event']=='measurement')
        r=next(r for r in reversed(self.records[:idx]) if r.get('event')=='driver_trace' and r['trace'].get('event')=='reply' and r['trace'].get('command')=='CHAN:OUTP?' and r['trace']['response']=='0')
        r['trace']['response']='1'
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_invalid_scpi_121_does_not_hide_behind_three_120(self):
        r=next(r for r in self.records if r.get('event')=='read' and r.get('command')=='SYST:ERR?' and r['response'].startswith('120,'))
        r['response']='121, "Other"'
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_ch3_status_quirk_is_accepted_but_conflicting_ques_is_not(self):
        r=next(r for r in self.records if r.get('event')=='measurement' and r['channel']==3)
        self.assertEqual(self.audit()['status'],'PASS')
        r['keithley_questionable_status']=2
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_physical_off_required(self):
        next(r for r in self.records if r['event']=='outcome')['physical_off_confirmed']=False
        self.assertEqual(self.audit()['status'],'FAIL')

if __name__=='__main__':unittest.main()
