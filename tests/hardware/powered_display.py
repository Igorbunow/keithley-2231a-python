"""Readable, evidence-derived powered U/I/P comparison; no instrument I/O."""
from __future__ import annotations

import math


def comparison_table(channel: int, mode: str, *, psu_v: float, psu_i: float,
                     psu_p: float, kel_v: float, kel_i: float, kel_p: float,
                     psu_scpi_p: float | None = None) -> str:
    """Format already-validated measurements; never synthesize missing readings."""
    vals = (psu_v, psu_i, psu_p, kel_v, kel_i, kel_p)
    if any(not math.isfinite(float(x)) for x in vals):
        raise ValueError('Non-finite measurement in powered comparison')
    lines = [f'CH{channel} {mode} — LIVE MEASURED VALUES (Keithley / KEL103)',
             '                 Keithley 2231A       KEL103          |difference|',
             f'U (V)            {psu_v:>10.5f}         {kel_v:>10.5f}       {abs(psu_v-kel_v):>9.5f}',
             f'I (A)            {psu_i:>10.6f}         {kel_i:>10.6f}       {abs(psu_i-kel_i):>9.6f}',
             f'P (W)            {psu_p:>10.5f}         {kel_p:>10.5f}       {abs(psu_p-kel_p):>9.5f}',
             f'Keithley regulation: {mode}; KEL103 mode: CR, INPUT ON.',
             'The Keithley power above is computed from measured U×I.']
    if psu_scpi_p is not None:
        if not math.isfinite(float(psu_scpi_p)):
            raise ValueError('Non-finite Keithley SCPI power measurement')
        lines.append(f'Keithley direct MEAS:POW? = {psu_scpi_p:.5f} W.')
    lines.append('Compare the actual Keithley U/I panel and KEL103 U/I/P panel '
                 'with their OWN columns; do not confirm any mismatch.')
    return '\n'.join(lines)
