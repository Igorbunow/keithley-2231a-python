"""Pure offline regression checks for the single-source qualification bench config."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import TestCase, mock
import tempfile

from bench_config import EXAMPLE, activate_bench, bench_summary, current_bench, load_bench
from equipment.factory import open_load, open_meter, open_psu, open_scope


class TestBenchConfig(TestCase):
    def test_read_example_without_opening_any_equipment(self):
        cfg = load_bench(EXAMPLE)
        self.assertEqual(cfg.psu.port, '/dev/ttyUSB1')
        self.assertEqual(cfg.psu.baudrate, 9600)
        self.assertEqual(cfg.load.transport, 'serial')
        self.assertEqual(cfg.meter.transport, 'tcp')
        self.assertEqual(cfg.scope.transport, 'escope_http')
        self.assertEqual(len(cfg.sha256), 64)

    def test_custom_ports_and_hosts_are_loaded_from_one_file(self):
        raw = EXAMPLE.read_text(encoding='utf-8')
        raw = raw.replace('/dev/ttyUSB1', '/dev/ttyUSB9')
        raw = raw.replace('/dev/ttyACM0', '/dev/ttyACM4')
        raw = raw.replace('192.168.22.112', 'dmm.example.net')
        raw = raw.replace('192.168.22.105', 'scope.example.net')
        raw = raw.replace('network_port = 5025', 'network_port = 19025')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'custom.toml'
            target.write_text(raw, encoding='utf-8')
            cfg = load_bench(target)
            self.assertEqual(cfg.psu.port, '/dev/ttyUSB9')
            self.assertEqual(cfg.load.port, '/dev/ttyACM4')
            self.assertEqual(cfg.meter.host, 'dmm.example.net')
            self.assertEqual(cfg.meter.network_port, 19025)
            self.assertEqual(cfg.scope.host, 'scope.example.net')
            self.assertIn('/dev/ttyUSB9', bench_summary(cfg))
            self.assertIn('dmm.example.net:19025', bench_summary(cfg))

    def test_invalid_config_cannot_create_instrument(self):
        raw = EXAMPLE.read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'invalid.toml'
            for changed in (
                    raw.replace('model = "DMM7510"', 'model = "DMM7510"\nsecret = "invalid"'),
                    raw.replace('transport = "serial"', 'transport = "wrong"', 1),
                    raw.replace('port = "/dev/ttyUSB1"', 'port = "BAD"'),
                    raw.replace('host = "192.168.22.112"', 'host = "http://bad/"'),
                    raw.replace('[devices.scope]', '[devices.other]'),
                    raw.replace('usb_vid = "10C4"', 'usb_vid = "WRONG"'),
            ):
                with self.subTest(changed=changed[:48]):
                    target.write_text(changed, encoding='utf-8')
                    with self.assertRaises((ValueError, TypeError)):
                        load_bench(target)

    def test_factory_builds_only_configured_interfaces(self):
        cfg = load_bench(EXAMPLE)
        other = replace(cfg, psu=replace(cfg.psu, port='/dev/ttyUSB6', timeout_s=3.0),
                        load=replace(cfg.load, port='/dev/ttyACM4', baudrate=57600),
                        meter=replace(cfg.meter, host='multimeter.example.net', network_port=19999),
                        scope=replace(cfg.scope, host='scope.example.net', network_port=8181))
        evidence = mock.Mock()
        evidence.path = Path('/tmp/non-existent/evidence.jsonl')
        with mock.patch('keithley2231a.SerialTransport') as serial_transport:
            open_psu(other)
            serial_transport.assert_called_once_with('/dev/ttyUSB6', baudrate=9600, timeout=3.0)
        with mock.patch('equipment.factory.KEL103Serial') as load_serial:
            open_load(other, evidence)
            self.assertEqual(load_serial.call_args.args, ('/dev/ttyACM4',))
            self.assertEqual(load_serial.call_args.kwargs['baudrate'], 57600)
        with mock.patch('keithley2231a.TcpTransport') as meter_transport, \
                mock.patch('equipment.factory.DMM7510') as dmm:
            open_meter(other, evidence)
            self.assertEqual(meter_transport.call_args.args,
                             ('multimeter.example.net', 19999))
            dmm.assert_called_once_with(meter_transport.return_value, evidence)
        with mock.patch('equipment.factory.TDS3064B') as scope:
            open_scope(other, evidence)
            self.assertEqual(scope.call_args.kwargs['host'], 'scope.example.net')
            self.assertEqual(scope.call_args.kwargs['port'], 8181)

    def test_qualification_scopes_have_no_runtime_dependencies_on_old_driver_paths(self):
        hardware = Path(__file__).resolve().parents[1] / 'hardware'
        self.assertFalse((hardware / 'dmm7510.py').exists())
        source = (hardware / 'channel_stage.py').read_text()
        self.assertNotIn('class KEL103UDP', source)
        self.assertNotIn('class KEL103Serial', source)
        source = (hardware / 'h05_stage.py').read_text()
        self.assertNotIn('class TDS3064B', source)
        # Local equipment adapters must stay outside the installable library.
        for source_file in (Path(__file__).resolve().parents[2] / 'src/keithley2231a').glob('*.py'):
            self.assertNotIn('equipment.', source_file.read_text())

    def test_serial_adapter_user_confirmation_matches_bench_config(self):
        from qualification import verify_serial_adapter
        cfg = load_bench(EXAMPLE)
        activate_bench(cfg)
        ports = [type('Port', (), {'device': cfg.psu.port, 'vid': 0x10C4, 'pid': 0xEA60})()]
        self.assertTrue(verify_serial_adapter(cfg.psu.port, 'CP2102 10C4:EA60', ports))
        with self.assertRaisesRegex(ValueError, 'configured for the bench'):
            verify_serial_adapter(cfg.psu.port, 'FT232 0403:6001', ports)

    def test_equipment_connections_are_absent_from_stage_source(self):
        hardware = Path(__file__).resolve().parents[1] / 'hardware'
        for path in hardware.glob('*stage.py'):
            source = path.read_text()
            with self.subTest(stage=path.name):
                self.assertNotIn('192.168.22.', source)
                self.assertNotIn('/dev/ttyUSB1', source)
                self.assertNotIn('/dev/ttyACM0', source)
