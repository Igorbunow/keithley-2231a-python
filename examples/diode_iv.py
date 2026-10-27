"""Operator-controlled diode I-V example. DOES NOT RUN until --run is given.

Use a correctly rated series resistor; never connect a diode directly without
checking the programmed current limit, polarity, DUT heating and wiring.
The PSU voltage is measured at terminals, not across the diode itself.
"""
from pathlib import Path
import argparse
from keithley2231a import (Profile, ChannelProfile, SerialTransport,
                            Keithley2231A, voltage_sweep)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--port', default='/dev/ttyUSB1')
p.add_argument('--run', action='store_true', help='Allow hardware output switching')
p.add_argument('--series-resistor', type=float, default=220.0)
p.add_argument('--max-voltage', type=float, default=3.0)
p.add_argument('--current-limit', type=float, default=.02)
p.add_argument('--csv', default='runs/diode_iv.csv')
args = p.parse_args()
if not args.run:
    p.error('NO hardware output was enabled. Review circuit, then explicitly add --run')
if not 0 < args.series_resistor or not 0 < args.max_voltage <= 5.0 or not 0 < args.current_limit <= .05:
    p.error('invalid limits; example deliberately restricts voltage to 5 V and current to 50 mA')
if args.max_voltage / args.series_resistor > args.current_limit:
    p.error('series resistor alone must bound worst-case current below the limit in this example')
step_count = int(round(args.max_voltage/.1))
voltages = [round(i*.1, 2) for i in range(step_count+1)]

profile = Profile('diode-low-energy-example', (
    ChannelProfile(args.max_voltage, args.current_limit, 0.0, args.current_limit),
    ChannelProfile(1.0, .1, 0.0, .01),
    ChannelProfile(1.0, .1, 0.0, .01)),
    lock_front_panel=True)

with Keithley2231A(SerialTransport(args.port, timeout=3)) as psu:
    psu.apply_profile(profile, confirmed_wiring='independent')  # ALWAYS global OFF and reconfiguration
    points = voltage_sweep(psu, 1, voltages, current_limit_a=args.current_limit,
                           settle_s=.5, csv_path=args.csv)
for point in points:
    u_diode = point.measured_v - point.measured_a*args.series_resistor
    print(f'{point.requested_v:5.2f} V supply -> {u_diode:7.3f} V diode estimate / {point.measured_a*1000:7.2f} mA')
print(f'CSV: {args.csv} (all outputs commanded OFF on completion)')
