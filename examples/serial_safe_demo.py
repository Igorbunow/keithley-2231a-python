"""No live load needed for stage-1 checks. Initial outputs remain OFF."""
from pathlib import Path
from keithley2231a import SerialTransport, Keithley2231A, Profile

profile = Profile.from_json(Path(__file__).with_name('safe_3ch.json'))

with Keithley2231A(SerialTransport('/dev/ttyUSB1', timeout=3)) as dev:
    print(dev.identity())
    dev.apply_profile(profile)
    print('Profile loaded; outputs remain OFF')
    dev.verify_profile()
    print('Profile verified. SCPI error:', dev.error())
    for ch in (1,2,3):
        print(dev.read_channel(ch))
    # Explicitly enable only after checking wired load and all limits:
    # dev.set_channel(1, voltage=1.00, current=0.100)
    # dev.output(1, True)
    # Acquisition(dev, interval_s=2, csv_path='experiment.csv').run(duration_s=60)
    # dev.output(1, False)
