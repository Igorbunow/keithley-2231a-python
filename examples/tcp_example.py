"""Compatible with a future *transparent* TCP-to-TTL-UART bridge on MCU."""
from pathlib import Path
from keithley2231a import TcpTransport, Keithley2231A, Profile

profile = Profile.from_json(Path(__file__).with_name('safe_3ch.json'))
with Keithley2231A(TcpTransport('psu-lab.local', 5025, timeout=3)) as dev:
    dev.apply_profile(profile, confirmed_wiring='independent')
    print(dev.identity())
    print(dev.read_channel(1))
