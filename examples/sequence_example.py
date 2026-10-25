"""Independent-channel delayed sequence. Requires explicit --run and a dummy DUT."""
from pathlib import Path
import argparse
from keithley2231a import SerialTransport, Keithley2231A, Profile, Acquisition

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--port',default='/dev/ttyUSB1')
p.add_argument('--run',action='store_true')
a=p.parse_args()
if not a.run: p.error('Refusing to enable outputs without explicit --run')
profile=Profile.from_json(Path(__file__).with_name('safe_3ch.json'))
with Keithley2231A(SerialTransport(a.port)) as dev:
    dev.apply_profile(profile)  # Off-first, NO automatic power-on
    dev.set_channel(1,voltage=1.0,current=.1)
    dev.set_channel(2,voltage=1.0,current=.1)
    dev.set_channel(3,voltage=1.0,current=.05)
    try:
        dev.sequence([(0,1,True),(.5,2,True),(.2,3,True)])
        run=Acquisition(dev,interval_s=2.0,csv_path='runs/sequence.csv')
        run.run(duration_s=20)
        print({ch: st.as_dict() for ch,st in run.stats.items()})
    finally:
        dev.emergency_off()
