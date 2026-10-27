"""Isolated Git tests for the opt-in incremental patch helper (no hardware)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[2] / "Scripts/apply_incremental_patch.sh"


class IncrementalPatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / 'repo'
        self.repo.mkdir()
        self._git('init', '-q')
        self._git('config', 'user.email', 'local@example.invalid')
        self._git('config', 'user.name', 'Patch test')
        (self.repo / 'base.txt').write_text('base\n')
        self._git('add', 'base.txt')
        self._git('commit', '-qm', 'baseline')
        (self.repo / 'base.txt').write_text('updated\n')
        self.patch = Path(self.directory.name) / 'change.patch'
        self.patch.write_bytes(self._git('diff', '--binary', binary=True).stdout)
        (self.repo / 'base.txt').write_text('base\n')
        self.paths = Path(self.directory.name) / 'stage_paths.txt'
        self.paths.write_text('base.txt\n')

    def _git(self, *args, binary=False):
        return subprocess.run(['git', *args], cwd=self.repo, check=True,
                              capture_output=True, text=not binary)

    def _run(self, *args):
        return subprocess.run(['bash', str(HELPER), *map(str, args)],
                              cwd=self.repo, text=True, capture_output=True)

    def test_check_is_nonmutating_and_apply_stages_exact_file(self):
        checked = self._run('--check', self.patch, self.paths)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('CHECK_PASS', checked.stdout)
        self.assertEqual(self._git('status', '--porcelain').stdout.strip(), '')
        applied = self._run(self.patch, self.paths)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn('status=PASS', applied.stdout)
        self.assertEqual(self._git('diff', '--cached', '--name-only').stdout.strip(), 'base.txt')
        self.assertEqual(self._git('diff', '--name-only').stdout.strip(), '')

    def test_wrong_manifest_fails_without_changes(self):
        self.paths.write_text('unrelated.txt\n')
        failed = self._run(self.patch, self.paths)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('does not exactly match', failed.stderr)
        self.assertEqual(self._git('status', '--porcelain').stdout.strip(), '')

    def test_unstaged_drift_refused_without_changes(self):
        (self.repo / 'base.txt').write_text('user edits\n')
        failed = self._run(self.patch, self.paths)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('tracked unstaged changes', failed.stderr)
        self.assertEqual((self.repo / 'base.txt').read_text(), 'user edits\n')

    def test_preexisting_staged_candidate_is_preserved(self):
        (self.repo / 'other.txt').write_text('stage me\n')
        self._git('add', 'other.txt')
        applied = self._run(self.patch, self.paths)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        staged = self._git('diff', '--cached', '--name-only').stdout.splitlines()
        self.assertEqual(staged, ['base.txt', 'other.txt'])

    def test_unrelated_untracked_file_is_preserved(self):
        (self.repo / 'local_notes').write_text('keep\n')
        applied = self._run(self.patch, self.paths)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((self.repo / 'local_notes').read_text(), 'keep\n')

    def test_unsafe_manifest_is_rejected(self):
        self.paths.write_text('../base.txt\n')
        failed = self._run(self.patch, self.paths)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('unsafe path', failed.stderr)

    def test_duplicate_paths_are_rejected(self):
        self.paths.write_text('base.txt\nbase.txt\n')
        failed = self._run(self.patch, self.paths)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('duplicate paths', failed.stderr)


if __name__ == '__main__':
    unittest.main()
