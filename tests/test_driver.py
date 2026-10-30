from examples.acquisition import ChannelStats
import unittest
from keithley2231a import (Profile, ProfileError, ChannelProfile, LimitsError, SeriesProfile, Reading)

def profile(wiring='independent', expected='auto'):
    return Profile('demo', (
        ChannelProfile(3.3,.2,1,.1,expected),
        ChannelProfile(5,.3,1,.1),
        ChannelProfile(3.3,.1,1,.05)), wiring=wiring)

class TestProfile(unittest.TestCase):
    def test_limits(self):
        with self.assertRaises(LimitsError): ChannelProfile(30, 4).validate(1)
        with self.assertRaises(LimitsError): ChannelProfile(6, 1).validate(3)
        with self.assertRaises(LimitsError): ChannelProfile(2, 1, 3).validate(1)
        with self.assertRaises(LimitsError): ChannelProfile(3, .2, .01, .3).validate(1)
        with self.assertRaises(Exception): ChannelProfile(float('nan'), .2).validate(1)
        with self.assertRaises(Exception): ChannelProfile(3.33, .2, .001, .1).validate(1)

    def test_series_profile_models_total_setpoint_and_soft_cap(self):
        series = SeriesProfile('series', (2., 3.), 4.5, .3, 2.5, .1,
                               ChannelProfile(4., .5, 2., .3))
        self.assertEqual(series.wiring, 'series')
        self.assertEqual(series.max_voltage, 4.5)
        with self.assertRaises(ProfileError):
            SeriesProfile('spoofed mode', (2., 3.), 4.5, .3, 2., .1,
                          ChannelProfile(4., .5, 0., .1), wiring='parallel')
        with self.assertRaises(ProfileError):
            SeriesProfile('spoofed access', (2., 3.), 4.5, .3, 2., .1,
                          ChannelProfile(4., .5, 0., .1), access='power_only')
        for kwargs in (
                {'max_voltage': 5.1},
                {'max_voltage': 5.0, 'initial_voltage': 5.01},
                {'max_current': 0.0},
                {'channel_voltage_limits': (31., 3.)},
                {'initial_voltage': 2.501},):
            values = dict(name='invalid', channel_voltage_limits=(2., 3.),
                          max_voltage=5., max_current=.3, initial_voltage=2.5,
                          initial_current=.1, channel3=ChannelProfile(4., .5, 2., .3))
            values.update(kwargs)
            with self.assertRaises((ValueError, TypeError)):
                SeriesProfile(**values)

    def test_json(self):
        p = Profile.from_json('examples/safe_3ch.json')
        self.assertEqual(p.channels[2].max_voltage, 3.3)
        self.assertEqual(p.wiring, 'independent')

class TestStats(unittest.TestCase):
    def make(self, timestamp, amps, volts=2, enabled=True):
        return Reading(1, 'utc', timestamp, volts, amps, volts*amps, 'CV', enabled, 'CH1')

    def test_welford_and_integration(self):
        s = ChannelStats()
        s.add(self.make(0,1))
        s.add(self.make(3600,3), max_gap_s=4000)
        self.assertAlmostEqual(s.current.mean,2)
        self.assertAlmostEqual(s.current.stddev,1)
        self.assertAlmostEqual(s.ampere_hours,2)
        self.assertAlmostEqual(s.watt_hours,4)
        s.reset_statistics()
        self.assertEqual(s.current.count,0)
        self.assertEqual(s.ampere_hours,2)
        s.reset_statistics(reset_energy=True)
        self.assertEqual(s.ampere_hours,0)

    def test_disconnected_gaps_are_not_integrated(self):
        s = ChannelStats()
        s.add(self.make(0,1))
        s.add(self.make(100,1), max_gap_s=10)
        self.assertEqual(s.skipped_gaps,1)
        self.assertEqual(s.ampere_hours,0)
        s.add(self.make(101,1,enabled=False), max_gap_s=10)
        self.assertEqual(s.ampere_hours,0)



class TestTransportNetwork(unittest.TestCase):
    def test_tcp_transparent_round_trip(self):
        import socket
        import threading
        from keithley2231a import TcpTransport
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('127.0.0.1', 0)); server.listen(1)
        port = server.getsockname()[1]
        got = []
        def worker():
            try:
                client, _ = server.accept()
                with client:
                    buf=b''
                    while not buf.endswith(b'\r\n'):
                        buf += client.recv(256)
                    got.append(buf)
                    client.sendall(b'Keithley,OK\n')
            finally: server.close()
        th=threading.Thread(target=worker,daemon=True); th.start()
        t=TcpTransport('127.0.0.1',port,timeout=1)
        try: self.assertEqual(t.query('*IDN?'), 'Keithley,OK')
        finally: t.close(); th.join(timeout=1)
        self.assertEqual(got, [b'*IDN?\r\n'])

    def test_serial_timeout_and_stale_input_require_new_transport(self):
        try:
            import serial
        except ImportError:
            self.skipTest('pyserial is not installed')
        from unittest.mock import Mock, patch
        from keithley2231a import SerialTransport, TransportError
        for stale in (False, True):
            with self.subTest(stale=stale):
                port = Mock()
                port.in_waiting = int(stale)
                port.readline.return_value = b'partial'
                port.write.side_effect = lambda value: len(value)
                with patch('serial.Serial', return_value=port):
                    io = SerialTransport('SIMULATED')
                with self.assertRaises(TransportError): io.query('*IDN?')
                port.readline.return_value = b'1\n'
                with self.assertRaises(TransportError): io.query('CHAN:OUTP?')
                with self.assertRaises(TransportError): io.write('CHAN:OUTP 1')
                io.close()

    def test_tcp_timeout_cannot_reuse_late_reply(self):
        from unittest.mock import Mock, patch
        from keithley2231a import TcpTransport, TransportError
        sock = Mock()
        sock.recv.side_effect = [TimeoutError('timeout'), b'1\n']
        with patch('socket.create_connection', return_value=sock):
            io = TcpTransport('SIMULATED', 5025)
        with self.assertRaises(TransportError): io.query('*IDN?')
        with self.assertRaises(TransportError): io.query('CHAN:OUTP?')
        with self.assertRaises(TransportError): io.write('CHAN:OUTP 1')
        io.close()


if __name__ == '__main__': unittest.main()
