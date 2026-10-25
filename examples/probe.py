"""Read-only instrument identification and state inspection (no voltage writes)."""
import argparse
from keithley2231a import SerialTransport

parser = argparse.ArgumentParser()
parser.add_argument('--port', default='/dev/ttyUSB1')
args = parser.parse_args()
with_device = SerialTransport(args.port, timeout=3)
try:
    # Selection is unchanged; only query commands, safe with a live load.
    for cmd in ('*IDN?', 'SYST:VERS?', 'INST:NSEL?', 'OUTP:STAT:ALL?',
                'OUTP:SER?', 'OUTP:PARA?', 'OUTP:TRAC?',
                'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                'CHAN:OUTP?', 'MEAS:VOLT?', 'MEAS:CURR?',
                'STAT:OPER:INST:ISUM1:COND?', 'SYST:ERR?'):
        try:
            print(f'{cmd:35s}: {with_device.query(cmd)}')
        except Exception as exc:
            print(f'{cmd:35s}: ERROR {type(exc).__name__}: {exc}')
            # On a query timeout, serial replies may be out of sync;
            # DO NOT continue sending SCPI commands after a timeout.
            break
finally:
    with_device.close()
