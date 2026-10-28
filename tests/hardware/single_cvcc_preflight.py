"""CH1-only H03 CV/CC planning card. No hardware I/O is permitted here."""
from __future__ import annotations


def describe(bench, *, execution_supported=False):
    cfg = bench.h03_single_cvcc
    if cfg is None:
        raise ValueError('qualification.h03_single_cvcc missing')
    cfg.validate()
    cv_est_i = cfg.cv_voltage_v / cfg.cv_resistance_ohm
    cc_est_v = cfg.cc_current_limit_a * cfg.cc_resistance_ohm
    return (
        f'[H03 CH1 single CV/CC / {"GATED LIVE" if execution_supported else "CONFIG ONLY"}] SHA256={bench.sha256}\n'
        f'CH1/CH2/CH3 initial V={cfg.initial_voltage_v}, I={cfg.initial_current_a}\n'
        f'CH1/CH2/CH3 voltage caps={cfg.voltage_cap_v}, current caps={cfg.current_cap_a}\n'
        f'CV: CH1 {cfg.cv_voltage_v:g} V / limit {cfg.cv_current_limit_a:g} A '
        f'/ KEL103 CR {cfg.cv_resistance_ohm:g} Ω; ideal ~{cv_est_i:.3f} A\n'
        f'CC: CH1 {cfg.cc_voltage_v:g} V / limit {cfg.cc_current_limit_a:g} A '
        f'/ KEL103 CR {cfg.cc_resistance_ohm:g} Ω; ideal ~{cc_est_v:.3f} V\n'
        f'Hardware max configured point: {cfg.max_power_w:g} W.\n'
        'No jumpers, no DUT; CH2/CH3 terminals free and OFF. '
        'KEL103 connected to CH1 only after explicit wiring approval. '
        'Do not move cables energized.\n'
        'Planned runner: verify PSU and load INPUT OFF; program capped profile; '
        'for CV and CC separately set CR only while INPUT OFF; require connection '
        'and panel gates; enable CH1 only; compare KEL103 U/I/P and status; '
        'return CH1 and load INPUT to OFF and verify readback.\n'
        + ('Execution requires separate interactive bench, wiring and per-point ON gates.'
         if execution_supported else
         'This variant cannot execute: no instrument connections or output enable.')
    )
