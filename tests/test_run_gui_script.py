import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RunGuiScriptTests(unittest.TestCase):
    def launch_rules(self, extra_env):
        # 가짜 가상환경의 python으로 바꿔치기해 스크립트가 자식에게 넘기는 환경을 본다.
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'bin' / 'python'
            fake.parent.mkdir(parents=True)
            fake.write_text('#!/bin/sh\nprintf "RULES=%s\\n" "$QT_LOGGING_RULES"\n')
            fake.chmod(0o755)
            env = {**os.environ, 'VIRTUAL_ENV': tmp, **extra_env}
            run = subprocess.run(['bash', str(ROOT / 'run_gui.sh')], env=env,
                                 text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            return run.stdout

    def test_wayland_textinput_noise_is_muted(self):
        out = self.launch_rules({})
        self.assertIn('RULES=qt.qpa.wayland.textinput=false', out)

    def test_user_logging_rules_are_kept(self):
        out = self.launch_rules({'QT_LOGGING_RULES': 'my.rule=true'})
        self.assertIn('RULES=qt.qpa.wayland.textinput=false;my.rule=true', out)


if __name__ == '__main__':
    unittest.main()
