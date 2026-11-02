import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'venv.ps1'


def pwsh_binary():
    for name in ('pwsh', 'powershell'):
        found = shutil.which(name)
        if found:
            return found
    return None


PWSH = pwsh_binary()


@unittest.skipUnless(PWSH, 'pwsh/powershell이 없어 venv.ps1 테스트를 건너뜁니다.')
class VenvPs1ScriptTests(unittest.TestCase):
    def run_pwsh(self, command, input=None, timeout=30):
        return subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-Command', command],
                              text=True, capture_output=True, timeout=timeout, input=input)

    def test_syntax_is_valid(self):
        script = str(SCRIPT).replace("'", "''")
        run = self.run_pwsh(
            '$tokens = $null; $errors = $null; '
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{script}', "
            '[ref]$tokens, [ref]$errors); '
            '"ERRORS={0};{1}" -f $errors.Count, ($errors -join "|")'
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.stdout.startswith('ERRORS=0'), run.stdout)

    def stage(self, tmp):
        """스크립트 복사본과 빈 프로젝트 루트를 준비한다."""
        root = Path(tmp) / 'proj'
        root.mkdir()
        staged = root / 'venv.ps1'
        staged.write_bytes(SCRIPT.read_bytes())
        return root, staged

    def script_paths(self, root):
        """스크립트와 같은 Join-Path 계산으로 가상환경 경로를 구한다."""
        quoted = str(root).replace("'", "''")
        run = self.run_pwsh(
            f"$venvDir = Join-Path '{quoted}' '.venv'; "
            "$activate = Join-Path $venvDir 'Scripts\\Activate.ps1'; "
            "$venvPython = Join-Path $venvDir 'Scripts\\python.exe'; "
            '$venvDir; $activate; $venvPython'
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout.splitlines()

    def make_fixture_venv(self, venv_dir, activate_path, python_path, marker=None):
        Path(activate_path).parent.mkdir(parents=True, exist_ok=True)
        content = f"$env:VIRTUAL_ENV = '{venv_dir}'\n"
        if marker is not None:
            content += f'"$env:VIRTUAL_ENV" | Out-File -FilePath \'{marker}\' -Encoding utf8\n'
        Path(activate_path).write_text(content, encoding='utf-8')
        Path(python_path).write_bytes(b'')

    def test_missing_environment_fails_without_exiting_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, staged = self.stage(tmp)
            run = self.run_pwsh(f". '{staged}'; 'RC=' + $LASTEXITCODE")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('RC=1', run.stdout)
            self.assertIn('가상환경', run.stderr)

    def test_dot_source_activates_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, staged = self.stage(tmp)
            venv_dir, activate, python = self.script_paths(root)
            self.make_fixture_venv(venv_dir, activate, python)
            run = self.run_pwsh(
                f". '{staged}'; 'VENV=' + $env:VIRTUAL_ENV; "
                "'FUNC=' + [bool](Get-Command Invoke-ProumanVenv -ErrorAction SilentlyContinue); "
                "'VAR=' + (Test-Path variable:proumanVenvStatus)"
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('가상환경 활성화', run.stdout)
            self.assertIn(f'VENV={venv_dir}', run.stdout)
            self.assertIn('FUNC=False', run.stdout)
            self.assertIn('VAR=False', run.stdout)

    def test_direct_execution_opens_activated_child_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, staged = self.stage(tmp)
            venv_dir, activate, python = self.script_paths(root)
            marker = Path(tmp) / 'nested.marker'
            self.make_fixture_venv(venv_dir, activate, python, marker)
            run = self.run_pwsh(f"& '{staged}'", input='exit\n', timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('가상환경이 활성화된 새 셸', run.stdout)
            self.assertEqual(marker.read_text(encoding='utf-8-sig').strip(), venv_dir)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows 경로 방식이 필요해 Windows에서만 실행합니다.')
    def test_already_active_keeps_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, staged = self.stage(tmp)
            scripts = root / '.venv' / 'Scripts'
            scripts.mkdir(parents=True)
            (scripts / 'Activate.ps1').write_text("$env:VIRTUAL_ENV = 'SHOULD-NOT-RUN'\n",
                                                  encoding='utf-8')
            (scripts / 'python.exe').write_bytes(b'')
            venv = str(root / '.venv')
            run = self.run_pwsh(
                f"$env:PATH = '{scripts}' + [System.IO.Path]::PathSeparator + $env:PATH; "
                f"$env:VIRTUAL_ENV = '{venv}'; "
                f". '{staged}'; 'VENV=' + $env:VIRTUAL_ENV"
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('이미 활성화됨', run.stdout)
            self.assertIn(f'VENV={venv}', run.stdout)


if __name__ == '__main__':
    unittest.main()
