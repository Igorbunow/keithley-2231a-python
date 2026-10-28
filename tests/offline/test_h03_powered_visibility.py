"""Offline regressions for powered display, bounded gates and full H03 audit."""
from __future__ import annotations

import contextlib
import copy
import io
from unittest import TestCase, mock

from powered_display import comparison_table
from common import operator_gate
from tools.audit_hw_scpi import audit_full_h03_records


def six_point_trace(*, new_enable_gates=True):
    """Minimal synthetic evidence; no physical device access or real serial ports."""
    a=[]
    def e(kind, **kwargs):
        a.append(dict(event=kind, test_id='H03', **kwargs))
    def scpi(cmd, write=False):
        e('driver_trace', trace=dict(event='write' if write else 'query', command=cmd))
        e('write' if write else 'query', command=cmd)
        if cmd not in ('SYST:ERR?','SYST:LOC'):
            e('driver_trace', trace=dict(event='query', command='SYST:ERR?'))
            e('query', command='SYST:ERR?')
            e('read', command='SYST:ERR?', response='0,"No error"')
    e('bench_connection_confirmation', token='MOCK BENCH')
    e('approval', token='MOCK WIRING')
    for ch in (1,2,3):
        for mode,r in (('CV',40),('CC',12)):
            scpi(f'INST:NSEL {ch}',True)
            e('operator_gate', channel=ch, mode=mode, connection='KEL103 only')
            e('kel103_write', command=':INP ON')
            e('kel103_setup_verified', channel=ch, mode=mode, resistance_ohm=r,
              input_state='ON')
            if new_enable_gates:
                e('operator_gate', channel=ch, mode=mode,
                  authorized_output=f'CH{ch} only')
            scpi('CHAN:OUTP 1',True)
            u,i=(4.,.1) if mode=='CV' else (3.,.25)
            e('measurement', channel=ch, mode=mode, keithley_voltage_v=u,
              keithley_current_a=i, keithley_power_api_w=u*i,
              keithley_power_scpi_w=u*i, kel103_voltage_v=u,
              kel103_current_a=i, kel103_power_w=u*i,
              keithley_status_mask=(8 if ch==3 else 9 if mode=='CV' else 10))
            e('operator_display_confirmation', channel=ch,
              keithley_panel_mode=mode)
            scpi('CHAN:OUTP 0',True)
            e('kel103_write', command=':INP OFF')
            if ch==3:
                e('status_caveat', channel=3, mode=mode,
                  operation_status=8, questionable_status=1 if mode=='CV' else 2,
                  library_regulation=mode)
    e('query_outcome', outcome='PASS')
    e('outcome', outcome='PASS', physical_off_confirmed=True)
    return a


class PoweredVisibilityTests(TestCase):
    def test_comparison_displays_both_instruments_and_differences(self):
        out = comparison_table(1, 'CC', psu_v=3.01663, psu_i=.249663,
                               psu_p=.75314, kel_v=3.0106, kel_i=.2508,
                               kel_p=.755, psu_scpi_p=.75336)
        for token in ('Keithley 2231A', 'KEL103', '3.01663', '3.01060',
                      '0.249663', '0.250800', '0.75314', '0.75500',
                      '0.75336', 'regulation: CC'):
            self.assertIn(token,out)
        self.assertNotIn('None',out)

    def test_powered_gate_times_out_and_does_not_accept_late_input(self):
        with mock.patch('builtins.input', return_value='MY TOKEN'), \
             mock.patch('common.time.monotonic', side_effect=[10., 132.]):
            with self.assertRaisesRegex(TimeoutError,'powered display confirmation timed out'):
                with contextlib.redirect_stdout(io.StringIO()):
                    operator_gate('MY TOKEN','Verify', powered_timeout_s=120)

    def test_native_tty_read_timeout_raises_without_continuing(self):
        class TTY:
            def isatty(self): return True
            def fileno(self): return 0
        tty=TTY()
        with mock.patch('common.sys.stdin', tty), \
             mock.patch('common.select.select', return_value=([],[],[])) as select, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(TimeoutError,'powered display confirmation timed out'):
                operator_gate('TOKEN','Powered display',powered_timeout_s=25)
        select.assert_called_once()

    def test_full_h03_synthetic_wire_audit_and_tamper(self):
        clean = six_point_trace()
        result=audit_full_h03_records(clean)
        self.assertEqual(result['status'],'PASS_WITH_REVIEW_NOTES')
        self.assertFalse(result['issues'])
        old=six_point_trace(new_enable_gates=False)
        old_result=audit_full_h03_records(old)
        self.assertEqual(old_result['status'],'PASS_WITH_REVIEW_NOTES')
        self.assertTrue(any('HISTORICAL_ON_GATE_SCOPE' in s for s in old_result['historical_deviations']))
        cases=[]
        tampered=copy.deepcopy(clean)
        next(r for r in tampered if r.get('event')=='driver_trace' and
             r.get('trace',{}).get('command')=='CHAN:OUTP 1')['trace']['command']='OUTP 1'
        cases.append(tampered)
        tampered=copy.deepcopy(clean)
        next(r for r in tampered if r.get('event')=='measurement')['kel103_current_a']=1.0
        cases.append(tampered)
        tampered=copy.deepcopy(clean)
        next(r for r in tampered if r.get('event')=='status_caveat')['questionable_status']=3
        cases.append(tampered)
        tampered=copy.deepcopy(clean)
        for idx,r in enumerate(tampered):
            if r.get('event')=='kel103_write' and r.get('command')==':INP OFF':
                del tampered[idx]; break
        cases.append(tampered)
        for altered in cases:
            with self.subTest(case=cases.index(altered)):
                self.assertEqual(audit_full_h03_records(altered)['status'],'FAIL')

    def test_explicit_enable_gate_is_not_replaced_by_connection_gate(self):
        evidence=six_point_trace()
        idx=next(i for i,x in enumerate(evidence) if x.get('event')=='operator_gate'
                 and 'authorized_output' in x)
        del evidence[idx]
        self.assertEqual(audit_full_h03_records(evidence)['status'],'FAIL')
