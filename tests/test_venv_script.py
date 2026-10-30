import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VenvScriptTests(unittest.TestCase):
    def test_source_activates_from_other_directory_and_is_idempotent(self):
        env = os.environ.copy()
        env.pop('VIRTUAL_ENV', None)
        script = ROOT / 'venv.sh'
        command = 'source "$1" && source "$1" && printf "ACTIVE=%s\\nPY=%s\\n" "$VIRTUAL_ENV" "$(command -v python)"'
        run = subprocess.run(['bash', '-c', command, 'bash', str(script)], cwd='/', env=env,
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'ACTIVE={ROOT}/.venv', run.stdout)
        self.assertIn(f'PY={ROOT}/.venv/bin/python', run.stdout)

    def test_missing_environment_fails_without_exiting_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'venv.sh'
            target.write_bytes((ROOT / 'venv.sh').read_bytes())
            command = 'source "$1"; rc=$?; printf "after=%s\\n" "$rc"'
            run = subprocess.run(['bash', '-c', command, 'bash', str(target)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0)
            self.assertIn('after=1', run.stdout)
            self.assertIn('가상환경', run.stderr)

    def test_direct_execution_opens_activated_child_shell(self):
        run = subprocess.run([str(ROOT / 'venv.sh')], input='printf "DIRECT=%s\\n" "$VIRTUAL_ENV"\nexit\n',
                             text=True, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'DIRECT={ROOT}/.venv', run.stdout)

    def test_different_active_environment_switches_to_project(self):
        env = {**os.environ, 'VIRTUAL_ENV': '/other/environment'}
        run = subprocess.run(['bash', '-c', 'source "$1" && printf "%s" "$VIRTUAL_ENV"',
                              'bash', str(ROOT / 'venv.sh')], env=env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.stdout.rstrip().endswith(str(ROOT / '.venv')))


if __name__ == '__main__':
    unittest.main()
