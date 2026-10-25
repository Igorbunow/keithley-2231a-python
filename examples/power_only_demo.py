"""Load `power_only.json`; require explicit --run to energize any terminal."""
from pathlib import Path
import argparse
import time
from keithley2231a import Keithley2231A, Profile, SerialTransport

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port',default='/dev/ttyUSB1')
parser.add_argument('--run',action='store_true',help='Explicitly enable real outputs')
parser.add_argument('--seconds',type=float,default=3.0)
args=parser.parse_args()
if not args.run: parser.error('Refusing to power outputs without --run')
if args.seconds < 0: parser.error('--seconds must be nonnegative')
profile=Profile.from_json(Path(__file__).with_name('power_only.json'))
with Keithley2231A(SerialTransport(args.port, timeout=3)) as psu:
    psu.apply_profile(profile)  # Forces ALL channels OFF
    print('Inspect your wiring and outputs: example profile is NOT universally safe.')
    psu.power_on()
    try: time.sleep(args.seconds)
    finally: psu.power_off()
