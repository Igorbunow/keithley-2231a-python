"""Inspect state without changing outputs or setpoints; enters Remote mode."""
import argparse
from keithley2231a import Keithley2231A, SerialTransport

parser = argparse.ArgumentParser()
parser.add_argument('--port', default='/dev/ttyUSB1')
args = parser.parse_args()
with_device = SerialTransport(args.port, timeout=3)
try:
    # The driver owns identity validation and Remote entry before SCPI inspection.
    psu = Keithley2231A(with_device, debug=True)
    print(f'Output states: {psu.output_states()}')
    # The following diagnostic queries do not change channel selection or setpoints.
    for cmd in ('*IDN?', 'SYST:VERS?', 'INST:NSEL?', 'OUTP:STAT:ALL?',
                'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
                'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                'CHAN:OUTP?', 'MEAS:VOLT?', 'MEAS:CURR?',
                'STAT:OPER:INST:ISUM1:COND?', 'SYST:ERR?'):
        try:
            print(f'{cmd:35s}: {psu.diagnostic_query(cmd)}')
        except Exception as exc:
            print(f'{cmd:35s}: ERROR {type(exc).__name__}: {exc}')
            # On a query timeout, serial replies may be out of sync;
            # DO NOT continue sending SCPI commands after a timeout.
            break
finally:
    with_device.close()
