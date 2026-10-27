"""Offline tests for independent, read-only bench inventory and CLI handling."""
from __future__ import annotations

from pathlib import Path
import contextlib
import io
import json
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/hardware'))

from bench_config import EXAMPLE, activate_bench, load_bench
from equipment import inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.config = load_bench(EXAMPLE)
        activate_bench(self.config)
        self.evidence = Mock()

    def test_all_probes_run_even_when_first_second_and_third_fail(self):
        executed = []
        def fake(name, exc=None):
            def probe(config, evidence):
                executed.append(name)
                if exc:
                    raise exc
                return f'{name} online'
            return probe
        results = inventory.run_inventory(self.config, self.evidence,
            probes=(('psu', fake('psu', OSError('busy USB port'))),
                    ('load', fake('load', TimeoutError('no reply'))),
                    ('meter', fake('meter', ValueError('TSP active'))),
                    ('scope', fake('scope'))))
        self.assertEqual(executed, ['psu', 'load', 'meter', 'scope'])
        self.assertEqual([r.status for r in results], ['FAIL', 'FAIL', 'FAIL', 'PASS'])
        self.assertEqual(results[0].error_type, 'OSError')
        self.assertEqual(self.evidence.add.call_count, 5)
        self.assertEqual(self.evidence.add.call_args.kwargs['passed'], 1)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            inventory.print_inventory(results, Path('inventory.jsonl'))
        self.assertIn('доступно 1/4', output.getvalue())
        self.assertIn('busy USB port', output.getvalue())

    def test_serial_identity_and_close_on_mismatch(self):
        port = Mock()
        port.query.return_value = 'ACME,Wrong,serial,1.0'
        with patch('qualification.verify_serial_adapter'), patch.object(inventory, 'open_psu', return_value=port):
            with self.assertRaisesRegex(ValueError, 'identity/model mismatch'):
                inventory._psu(self.config, self.evidence)
        port.query.assert_called_once_with('*IDN?')
        port.close.assert_called_once_with()

    def test_load_read_only_and_close_on_failure(self):
        load = Mock()
        load.identify.return_value = 'KORAD,KEL103,SN123,1.0'
        load.input_state.side_effect = OSError('failed input-state query')
        with patch.object(inventory, 'open_load', return_value=load):
            with self.assertRaises(OSError):
                inventory._load(self.config, self.evidence)
        load.identify.assert_called_once_with()
        load.input_state.assert_called_once_with()
        load.set_input.assert_not_called()
        load.close.assert_called_once_with()

    def test_meter_reuses_adapter_identify_and_closes(self):
        meter = Mock()
        meter.identify.return_value = 'KEITHLEY INSTRUMENTS,MODEL DMM7510,1234,1.0'
        with patch.object(inventory, 'open_meter', return_value=meter):
            outcome = inventory._meter(self.config, self.evidence)
        self.assertIn('[REDACTED]', outcome)
        meter.identify.assert_called_once_with()
        meter.close.assert_called_once_with()

    def test_scope_uses_existing_adapter_without_any_setting_changes(self):
        scope = Mock()
        scope.command.return_value = 'TEKTRONIX,TDS 3064B,1234,FW'
        with patch.object(inventory, 'open_scope', return_value=scope):
            value = inventory._scope(self.config, self.evidence)
        self.assertIn('TDS 3064B', value)
        scope.command.assert_called_once_with('*IDN?')
        scope.configure.assert_not_called()

    def test_dmm_probe_does_not_configure_or_reset(self):
        from equipment.dmm7510 import DMM7510
        transport = Mock()
        transport.query.side_effect = ['KEITHLEY INSTRUMENTS,MODEL DMM7510,SN,1.0', 'SCPI']
        meter = DMM7510(transport, Mock())
        self.assertIn('DMM7510', meter.identify())
        self.assertEqual(transport.query.call_count, 2)
        self.assertEqual([call.args[0] for call in transport.query.call_args_list], ['*IDN?', '*LANG?'])
        transport.write.assert_not_called()

    def test_cli_inventory_default_dry_run_and_execute_reports_all(self):
        import qualification
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = qualification.main(['inventory', '--config', str(EXAMPLE)])
        self.assertEqual(rc, 0)
        self.assertIn('DRY RUN', output.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder) / 'inventory.jsonl'
            output = io.StringIO()
            original_runner = inventory.run_inventory
            def fake_run(config, logger):
                self.assertEqual(logger.path, evidence)
                return original_runner(config, logger,
                    probes=(('psu', lambda *_: 'Keithley ok'),
                            ('load', lambda *_: (_ for _ in ()).throw(OSError('busy'))),
                            ('meter', lambda *_: 'DMM ok'),
                            ('scope', lambda *_: 'scope ok')))
            with patch('equipment.inventory.run_inventory', side_effect=fake_run), \
                    contextlib.redirect_stdout(output):
                rc = qualification.main(['inventory', '--execute', '--config', str(EXAMPLE),
                                         '--evidence', str(evidence)])
            self.assertEqual(rc, 2)
            lines = [json.loads(line) for line in evidence.read_text().splitlines()]
            self.assertEqual(len([x for x in lines if x['event'] == 'equipment_probe']), 4)
            self.assertIn('доступно 3/4', output.getvalue())

if __name__ == '__main__':
    unittest.main()
