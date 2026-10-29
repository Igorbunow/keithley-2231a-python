"""R3.5.34: prevent experimental O02/obsolete roadmap regressing into the core."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src' / 'keithley2231a'
DRAFT = ROOT / 'experiments' / 'o02_dual_psu' / 'dual_offline.py'


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_single_authoritative_roadmap_and_no_live_links_to_old_file(self):
        self.assertFalse((ROOT / 'docs' / 'ROADMAP.ru.md').exists())
        self.assertTrue((ROOT / 'ROADMAP.md').exists())
        for path in (ROOT / 'README.md', ROOT / 'docs' / 'README.ru.md'):
            self.assertNotIn('](docs/ROADMAP.ru.md)', path.read_text(encoding='utf-8'))
            self.assertNotIn('](ROADMAP.ru.md)', path.read_text(encoding='utf-8'))

    def test_draft_outside_installable_package_with_no_old_import(self):
        self.assertTrue(DRAFT.is_file())
        self.assertFalse((SRC / 'dual_offline.py').exists())
        self.assertIsNone(importlib.util.find_spec('keithley2231a.dual_offline'))
        self.assertIn('where = ["src"]', (ROOT / 'pyproject.toml').read_text())
        from setuptools import find_packages
        self.assertNotIn('experiments', find_packages(where=str(ROOT / 'src')))

    def test_draft_does_not_depend_on_production_package(self):
        tree = ast.parse(DRAFT.read_text(encoding='utf-8'))
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        for node in imports:
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, 'Experimental model must be self-contained')
                self.assertFalse((node.module or '').startswith('keithley2231a'))
            else:
                self.assertFalse(any(x.name.startswith('keithley2231a') for x in node.names))

    def test_core_has_no_import_of_experimental_project(self):
        for path in SRC.glob('*.py'):
            self.assertNotIn('experiments.', path.read_text(encoding='utf-8'))

    def test_cli_is_offline_only_and_the_new_draft_import_is_used(self):
        from experiments.o02_dual_psu.dual_offline import simulate_pair
        self.assertEqual(simulate_pair().outcome, 'NOT_RUN')
        cli = (ROOT / 'tools' / 'o02_offline.py').read_text(encoding='utf-8')
        self.assertIn('from experiments.o02_dual_psu.dual_offline import', cli)
        self.assertNotIn("add_argument('--execute'", cli)
        self.assertNotIn('keithley2231a.dual_offline', cli)

    def test_r4_1_is_documented_but_does_not_register_live_scpi(self):
        coverage = (ROOT / 'docs' / 'SCPI_COVERAGE.md').read_text(encoding='utf-8')
        self.assertIn('077100401', coverage)
        self.assertIn('CAL:SECure', coverage)
        self.assertIn('SYST:MODUle?', coverage)
        self.assertGreaterEqual(coverage.count('| **W** |') + coverage.count('| **U** |') +
                                coverage.count('| **R** |') + coverage.count('| **X** |'), 80)
        self.assertIn('O02-B/C', (ROOT / 'ROADMAP.md').read_text())


if __name__ == '__main__':
    unittest.main()
