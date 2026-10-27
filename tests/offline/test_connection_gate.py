"""Offline checks for the single, reusable connection gate of all H stages."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench_config import EXAMPLE, load_bench
from connection_gate import (connection_card, connection_token, required_devices,
                             verify_serial_adapter)


class ConnectionGateTests(unittest.TestCase):
    def setUp(self):
        self.bench = load_bench(EXAMPLE)

    def test_instrument_inventory_covers_nested_adapters_and_recall(self):
        expected = {
            'H01': ('psu',), 'H02': ('psu',), 'H03': ('psu', 'load'),
            'H04': ('psu',), 'H04P': ('psu', 'load'),
            'H05': ('psu', 'load', 'scope'), 'H06': ('psu', 'load'),
            'H07': ('psu', 'load'), 'H08': ('psu', 'meter'),
            'H09': ('psu',), 'H10': ('psu',), 'H11': ('psu', 'load'),
            'H12': ('psu',),
        }
        self.assertEqual({stage: required_devices(stage) for stage in expected}, expected)
        self.assertEqual(required_devices('H06', recall=True), ('psu',))
        for stage in ('H09', 'H10'):
            self.assertEqual(required_devices(stage, powered=True), ('psu', 'load', 'meter'))
        self.assertEqual(required_devices('H10', high_current=True), ('psu', 'load', 'meter'))
        with self.assertRaises(ValueError):
            required_devices('H99')

    def test_connection_card_is_derived_entirely_from_config(self):
        altered = replace(self.bench,
                          psu=replace(self.bench.psu, port='/dev/ttyUSB77',
                                      baudrate=19200, timeout_s=3.5, usb_pid='BEEF'),
                          meter=replace(self.bench.meter, host='dmm.example.test',
                                        network_port=6123, timeout_s=6),
                          sha256='abcdef1234567890' * 4)
        card = connection_card(altered, 'H08', required_devices('H08'))
        for part in ('/dev/ttyUSB77', '19200', '10C4:BEEF', 'timeout=3.5 s',
                     'dmm.example.test:6123', 'timeout=6 s', 'ABCDEF123456', 'CONFIRM BENCH H08'):
            self.assertIn(part, card)
        self.assertNotIn('/dev/ttyUSB1', card)
        self.assertNotIn('KEL103 (', card)
        self.assertNotIn('TDS3064B (', card)

    def test_token_is_bound_to_stage_variant_and_config_bytes(self):
        ordinary = connection_token(self.bench, 'H06')
        self.assertNotEqual(ordinary, connection_token(self.bench, 'H06', recall=True))
        self.assertNotEqual(ordinary, connection_token(replace(self.bench, sha256='0'*64), 'H06'))
        self.assertNotEqual(connection_token(self.bench, 'H10', powered=True),
                            connection_token(self.bench, 'H10', high_current=True))

    def test_wrong_serial_path_vid_pid_or_missing_usb_fails_closed(self):
        def port(device, vid, pid):
            return type('Port', (), {'device': device, 'vid': vid, 'pid': pid})()
        self.assertTrue(verify_serial_adapter(self.bench.psu, [port(self.bench.psu.port, 0x10C4, 0xEA60)]))
        with self.assertRaisesRegex(ValueError, 'observed .* configured'):
            verify_serial_adapter(self.bench.psu, [port(self.bench.psu.port, 0x0403, 0x6001)])
        with self.assertRaisesRegex(ValueError, 'not present'):
            verify_serial_adapter(self.bench.psu, [port('/dev/ttyUSB100', 0x10C4, 0xEA60)])
        with self.assertRaisesRegex(ValueError, 'requires serial'):
            verify_serial_adapter(self.bench.meter, [])

    def test_live_run_refuses_default_example_bench_before_any_io(self):
        import qualification as q
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')), \
             patch('builtins.input', side_effect=AssertionError('asked for input')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(q.main(['run', 'H01', '--execute']), 2)
        self.assertIn('bench.example.toml', output.getvalue())

    def test_main_rejects_unconfirmed_connections_without_opening_any_port(self):
        import qualification as q
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'bench.local.toml'
            local.write_bytes(EXAMPLE.read_bytes())
            with patch.object(q.sys.stdin, 'isatty', return_value=True), \
                 patch.object(q, 'verify_serial_adapter', return_value=True), \
                 patch.object(q, 'open_transport', side_effect=AssertionError('port opened')), \
                 patch('builtins.input', return_value='wrong token'), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(q.main(['run', 'H01', '--config', str(local), '--execute']), 2)
            self.assertIn('BLOCKED: соединения не подтверждены', output.getvalue())

    def test_main_rejects_usb_id_mismatch_before_asking_operator(self):
        import qualification as q
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'bench.local.toml'
            local.write_bytes(EXAMPLE.read_bytes())
            with patch.object(q.sys.stdin, 'isatty', return_value=True), \
                 patch.object(q, 'verify_serial_adapter', side_effect=ValueError('USB mismatch')), \
                 patch.object(q, 'open_transport', side_effect=AssertionError('port opened')), \
                 patch('builtins.input', side_effect=AssertionError('asked for input')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(q.main(['run', 'H01', '--config', str(local), '--execute']), 2)
            self.assertIn('BLOCKED: USB mismatch', output.getvalue())

    def test_h01_gate_records_config_confirmation_and_keeps_wiring_gate(self):
        import qualification as q
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'docs/hardware_reports').mkdir(parents=True)
            local = root / 'bench.local.toml'
            local.write_bytes(EXAMPLE.read_bytes())
            inputs = [connection_token(self.bench, 'H01'), q.TOKEN, q.FINAL_TOKEN]
            with patch.object(q, 'ROOT', root), \
                 patch.object(q, 'software_metadata', return_value={'source_sha256': 'offline'}), \
                 patch.object(q.sys.stdin, 'isatty', return_value=True), \
                 patch.object(q, 'verify_serial_adapter', return_value=True) as verifier, \
                 patch.object(q, 'open_transport', return_value=object()), \
                 patch.object(q, 'h01', return_value='PASS') as runner, \
                 patch('builtins.input', side_effect=inputs), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(q.main(['run', 'H01', '--config', str(local), '--execute']), 0)
            verifier.assert_called_once_with(self.bench.psu)
            runner.assert_called_once()
            log = next((root/'reports/hardware').glob('*/events.jsonl'))
            events = [json.loads(line) for line in log.read_text().splitlines()]
            gate = next(event for event in events if event['event'] == 'bench_connection_confirmation')
            self.assertEqual(gate['devices'], ['psu'])
            self.assertEqual(gate['sha256'], self.bench.sha256)
            self.assertEqual(gate['serial_os_verified'], ['psu'])
            self.assertTrue(any(event['event'] == 'approval' and event['token'] == q.TOKEN
                                for event in events))


if __name__ == '__main__':
    unittest.main()
