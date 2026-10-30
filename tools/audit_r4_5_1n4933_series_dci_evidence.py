#!/usr/bin/env python3
"""Fail-closed read-only R45FG original DMM7510 series DCI evidence audit.

Checks source hashes, SCPI stage and physical wire trace, independent DMM
configuration and measured current, bounded power enable and verified shutdown.
No equipment I/O, calibration, or causal claim about PSU hardware.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_r4_5_powered_step_evidence import AuditError, need, number, one, matching_pairs
from audit_r4_5_1n4933_evidence import load_original, close
from audit_r4_5_1n4933_ballast_evidence import check_wire, PSU_ALLOWED_WRITES, BENCH_SHA

EVENTS = {
    'start','pre_open_approvals','query','read','write','driver_trace','psu_identity',
    'dmm7510_query','dmm7510_read','dmm7510_write','dmm7510_command_set',
    'dmm7510_configuration','operator_confirmation','off_state_dmm_current',
    'dci_series_raw_sample','dci_series_point','shutdown_psu','outcome',
}
CODE_SHA='8abd22c7ffdb3011422ce696e04d9bcd33c960d8'
RUNNER_SHA='d1ec3bbddaa1d3b200e61d96d346eb5ae9dfef4a814d56eb126d312370ab444c'
STAGE_SHA='6552c0c36dfd2bc0d3b7e160c18d672c9f0db6f1a3f3e6621868614e9c030fba'
PLAN_SHA='00310d77f5207bd656098bc31381a2a078289a2571ca3a3de27bf7e1ba359e1e'
DMM_WRITES=('*RST', ':SENS:FUNC "CURR"', ':SENS:CURR:RANG:AUTO OFF',
            ':SENS:CURR:RANG 0.01', ':SENS:CURR:NPLC 1', ':SENS:CURR:AZER ON',
            ':SENS:CURR:AVER OFF', ':SENS:CURR:REL:STAT OFF')
PSU_WRITES=(PSU_ALLOWED_WRITES - {'CURR 0.020'}) | {'CURR 0.010'}


def unique(rows, event):
    return one([r for r in rows if r.get('event')==event], event)


def audit_dmm(rows):
    pairs=matching_pairs(rows, 'dmm7510_query','dmm7510_read')
    writes=[r['command'] for r in rows if r['event']=='dmm7510_write']
    need(tuple(writes)==DMM_WRITES, 'Unreviewed DMM current configuration')
    values={}
    for _,r in pairs:
        values.setdefault(r['command'], []).append(r['response'])
    need(len(values.get(':SYST:ERR?',[]))>=12 and
         all(v.split(',',1)[0].strip()=='0' for v in values[':SYST:ERR?']),
         'DMM error polling failed')
    need(values.get('*LANG?')==['SCPI'] and
         len(values.get('*IDN?',[]))==1 and
         values['*IDN?'][0].startswith('KEITHLEY INSTRUMENTS,MODEL DMM7510,') and
         values.get(':ROUT:TERM?')==['FRON'], 'Wrong DMM identity/language/terminals')
    for command, expected in ((':SENS:FUNC?','CURR:DC'),
                               (':SENS:CURR:RANG?',.01),
                               (':SENS:CURR:NPLC?',1),
                               (':SENS:CURR:REL:STAT?',0),
                               (':SENS:CURR:RANG:AUTO?',0)):
        found=values.get(command,[])
        need(len(found)==1,'DMM settings readback missing')
        if isinstance(expected,str): need(found==[expected], 'DMM function mismatch')
        else: close(found[0],expected,1e-9,'DMM setting '+command)
    config=unique(rows,'dmm7510_configuration')
    need(config.get('outcome')=='PASS' and config.get('function')=='DC current' and
         config.get('terminals')=='front' and number(config.get('range_a'))==.01 and
         number(config.get('nplc'))==1 and
         config.get('relative') is False and config.get('autorange') is False and
         config.get('physical_amps_jack')=='OPERATOR_CLAIM_ONLY',
         'DMM current setup mismatch')
    samples=values.get(':READ?',[])
    need(len(samples)==2,'Missing or extra direct DCI samples')
    return {'off':number(samples[0]),'powered':number(samples[1]),
            'dmm_error_polls':len(values[':SYST:ERR?'])}


def audit(rows):
    need(100<len(rows)<=8000 and all(type(r)==dict and r.get('test_id')=='R45FG' and
        r.get('event') in EVENTS for r in rows), 'Mixed or unsupported R45FG evidence')
    timestamps=[number(r.get('monotonic_s')) for r in rows]
    need(all(a<=b for a,b in zip(timestamps,timestamps[1:])), 'Nonmonotonic event timestamps')
    need(rows[0] is unique(rows,'start') and rows[-1] is unique(rows,'outcome'),
         'Missing anchored start/outcome')
    start=rows[0]; sw=start.get('software') or {}
    need(start.get('variant')=='1N4933_FRONT_DCI_SERIES_SINGLE_POINT' and
         sw.get('sha')==CODE_SHA and sw.get('runner_sha256')==RUNNER_SHA and
         sw.get('stage_sha256')==STAGE_SHA and
         start.get('config_sha256')==BENCH_SHA and
         start.get('plan_sha256')==PLAN_SHA and
         start.get('dmm_role')=='FRONT_10MA_DCI_AMPS_INPUT_LO_IN_SERIES' and
         start.get('current_accuracy')=='UNQUALIFIED' and
         start.get('operator_physical_claim_only') is True and
         start.get('note')=='KEL103 disconnected; DMM series ammeter; burden not measured',
         'Source/plan/provenance mismatch')
    close(start.get('commanded_preset_v'),.9,1e-9,'Preset')
    close(start.get('current_limit_a'),.01,1e-9,'Current limit')
    close(start.get('ballast_ohm'),99.86,1e-9,'Ballast')
    need(unique(rows,'pre_open_approvals').get('gates')==['BENCH','INITIAL'],
         'Missing initial approvals')
    gates=[r['gate'] for r in rows if r['event']=='operator_confirmation']
    need(gates==['POWER','FINAL'],'Operator gate order mismatch')
    need(unique(rows,'shutdown_psu').get('state')=='VERIFIED_OFF_BY_READBACK',
         'Missing verified PSU shutdown')
    end=rows[-1]
    need(end.get('outcome')=='PASS' and end.get('physical_off_confirmed') is True and
         end.get('status')=='HW NOT_REVIEWED' and
         not [r for r in rows if r['event']=='stage_error'],
         'Not a successful single point with physical OFF')
    wire=check_wire(rows, successful=True, allowed_writes=PSU_WRITES)
    dmm=audit_dmm(rows)
    power=next(r for r in rows if r.get('gate')=='POWER')
    final=next(r for r in rows if r.get('gate')=='FINAL')
    need(number(power['monotonic_s'])<wire['enabled_at']<wire['disabled_at']<=
         number(unique(rows,'shutdown_psu')['monotonic_s'])<number(final['monotonic_s']),
         'Unsafe power/shutdown sequence')
    off=unique(rows,'off_state_dmm_current')
    close(off['dmm_current_a'], dmm['off'], 1e-12,'DMM OFF baseline')
    need(abs(dmm['off'])<=.0002,'DMM OFF sample out of range')
    raw=unique(rows,'dci_series_raw_sample')
    point=unique(rows,'dci_series_point')
    need(raw.get('enabled') is True and raw.get('regulation')=='CV' and
         raw.get('note')=='UNVALIDATED_SEQUENTIAL_SAMPLES', 'DMM raw point not CV')
    for key, measured in (('psu_voltage_v',wire['psu_meas_voltage']),
                          ('psu_current_a',wire['psu_meas_current']),
                          ('dmm_direct_current_a',dmm['powered'])):
        close(raw[key],measured,1e-9,'Raw '+key)
        close(point[key],measured,1e-9,'Accepted '+key)
    need(0<=wire['psu_meas_current']<=.012 and
         .0002<=dmm['powered']<=.008 and
         abs(wire['psu_meas_voltage']-.9)<=.05,
         'Invalid measured current/voltage envelope')
    need(point.get('current_method')=='FRONT_10MA_DCI_AMPS_INPUT_LO_IN_SERIES' and
         point.get('current_accuracy')=='UNQUALIFIED' and
         point.get('burden_voltage')=='NOT_MEASURED' and
         point.get('causal_conclusion')=='PENDING_INDEPENDENT_AUDIT' and
         point.get('qualification')=='SINGLE_SERIES_DCI_DIAGNOSTIC_ONLY',
         'Unsupported accuracy/causal claim')
    close(point['psu_minus_dmm_current_a'],wire['psu_meas_current']-dmm['powered'],
          1e-9,'Independent DCI residual')
    gap=(number(raw['dmm_monotonic_s'])-number(raw['psu_monotonic_s']))*1000
    close(point['psu_dmm_gap_ms'], gap, 1e-5,'Sequential gap')
    need(0<=gap<1000 and
         wire['enabled_at']<=number(raw['psu_monotonic_s'])<=
         number(raw['dmm_monotonic_s'])<=wire['disabled_at'] and
         number(raw['monotonic_s'])<number(point['monotonic_s'])<wire['disabled_at'],
         'Raw and accepted point sequence mismatch')
    close(point['powered_duration_s'], number(point['monotonic_s'])-wire['enabled_at'],
          .2,'Powered time checkpoint')
    need(0<wire['disabled_at']-wire['enabled_at']<=90,
         'Powered exposure exceeds limit')
    psu_ma=wire['psu_meas_current']*1000
    dmm_ma=dmm['powered']*1000
    residual=psu_ma-dmm_ma
    # Manufacturer spec at 25±5C: readback ±(0.2% of reading + 10mA).
    # This is a compatibility bound, NOT a verified calibration result.
    spec_ma=10+.002*abs(psu_ma)
    return {'status':'INDEPENDENT_SERIES_DCI_EVIDENCE_REVIEW_PASS',
            'source_current_accuracy':'UNQUALIFIED',
            'scope':'ONE_POINT_DIAGNOSTIC_NOT_CURRENT_CALIBRATION',
            'source_readback_spec_at_25C_pm5C_ma':spec_ma,
            'observed_difference_within_nominal_spec':abs(residual)<=spec_ma,
            'psu_voltage_v':wire['psu_meas_voltage'],
            'psu_current_ma':psu_ma,'dmm_direct_current_ma':dmm_ma,
            'psu_minus_dmm_ma':residual,
            'dmm_off_current_ua':dmm['off']*1e6,
            'psu_error_polls':wire['psu_error_polls'],
            'dmm_error_polls':dmm['dmm_error_polls'],
            'powered_duration_s':wire['disabled_at']-wire['enabled_at'],
            'physical_off_confirmed':True,
            'notes':['Physical AMPS jack and wiring are operator-confirmed, not SCPI-verifiable',
                     'Readback spec is not a calibration and reference temperature was not logged',
                     'The source voltage and DMM current are sequential samples',
                     'No burden-voltage measurement or causal identification']}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--evidence',type=Path,required=True)
    ap.add_argument('--sha256',required=True)
    args=ap.parse_args(argv)
    try:
        rows, digest=load_original(args.evidence,args.sha256,allowed_events=EVENTS)
        result=audit(rows)
    except (AuditError,KeyError,TypeError,ValueError,OverflowError,
            AttributeError,OSError,ZeroDivisionError) as exc:
        print(f'R4.5-F-G ORIGINAL EVIDENCE REJECTED: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-G INDEPENDENT SERIES DCI EVIDENCE REVIEW PASS (OFFLINE)')
    print(json.dumps({'source_sha256':digest,**result},indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
