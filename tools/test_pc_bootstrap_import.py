"""Verify the standalone pretask imports under isolated Python, without running it.

注意：这里绝不能真的跑 main()。在 Windows 上它会走到 api.launch()，那是真的去
拉起游戏。需要验证 main 的收场行为时一律替换掉 _run。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def isolated(code, *arguments):
    """在隔离解释器里跑一段代码，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8=1", "-c", code, *[str(a) for a in arguments]],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )


class BootstrapImportTests(unittest.TestCase):
    script = ROOT / "agent/pc_bootstrap.py"

    def test_isolated_interpreter_from_unrelated_working_directory(self):
        code = """
import json
import runpy
import sys
from pathlib import Path
script = Path(sys.argv[1]).resolve()
assert str(script.parent) not in sys.path
runpy.run_path(str(script), run_name='bootstrap_import_test')
import startup.common
import importlib.abc
class BlockWindows(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('winreg', 'ctypes', 'startup.win32', 'maa'):
            raise AssertionError('Unexpected native import: ' + fullname)
sys.meta_path.insert(0, BlockWindows())
sys.platform = 'linux'
import startup.pc_install
assert Path(startup.common.__file__).resolve() == script.parent / 'startup/common.py'
assert 'startup.win32' not in sys.modules
assert 'utils.host_watchdog' not in sys.modules
assert 'maa' not in sys.modules
print(json.dumps({'startup': str(Path(startup.common.__file__).resolve())}))
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8=1", "-c", code, str(self.script)],
                cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["startup"]),
                         self.script.parent.resolve() / "startup/common.py")

    def test_boot_log_records_cwd_argv_and_interpreter(self):
        # 这一行是线上排查「pretask 到底有没有起来」的唯一读数，必须自己不会失败。
        code = """
import runpy
import sys
from pathlib import Path
script, workspace = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
mod = runpy.run_path(str(script), run_name='bootstrap_import_test')
# run_name 不是 __main__，所以导入期绝不能落盘，否则纯 import 测试会污染仓库。
assert not (workspace / 'debug').exists()
boot = mod['_boot_log']
boot.__globals__['AGENT_DIR'] = workspace / 'agent'
log_dir = workspace / 'debug' / 'pc_bootstrap'
assert boot(note='probe') == log_dir
text = (log_dir / 'custom.log.pc_bootstrap.log').read_text(encoding='utf-8')
for marker in ('[boot]', 'cwd=', 'argv=', 'exe=', 'file=', 'note=probe'):
    assert marker in text, marker
# 首选目录不可写时退到模块目录，并且绝不抛异常。
blocked = workspace / 'blocked'
blocked.write_text('not a directory', encoding='utf-8')
assert boot(blocked) == log_dir
assert not (workspace / 'debug' / 'pc_bootstrap.log').exists()
print('ok')
"""
        with tempfile.TemporaryDirectory() as directory:
            result = isolated(code, self.script, directory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_never_propagates_and_logs_the_reason(self):
        # pretask 永远以 0 退出：协议对退出码没有规定，赌不得上层软件的行为。
        code = """
import runpy
import sys
from pathlib import Path
script, workspace = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
mod = runpy.run_path(str(script), run_name='bootstrap_import_test')
globals_ = mod['_boot_log'].__globals__
globals_['AGENT_DIR'] = workspace / 'agent'
globals_['_boot_log'](note='before-main')

def boom(report):
    raise RuntimeError('boom')

globals_['_run'] = boom
globals_['main']()
if sys.platform == 'win32':
    text = (workspace / 'debug' / 'pc_bootstrap' / 'custom.log.pc_bootstrap.log').read_text(encoding='utf-8')
    assert 'note=before-main' in text, text
    assert 'boom' in text, text
    assert '不阻断任务队列' in text, text
    import os
    assert f'pid={os.getpid()}' in text, text
print('ok')
"""
        with tempfile.TemporaryDirectory() as directory:
            result = isolated(code, self.script, directory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_boot_log_falls_back_to_temp_module_directory(self):
        code = """
import os
import runpy
import sys
from pathlib import Path
script, workspace = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
mod = runpy.run_path(str(script), run_name='bootstrap_import_test')
boot = mod['_boot_log']
boot.__globals__['AGENT_DIR'] = workspace / 'agent'
(workspace / 'debug').write_text('blocked', encoding='utf-8')
os.environ['TEMP'] = str(workspace / 'temp')
fallback = workspace / 'temp' / 'MFABD2' / 'pc_bootstrap'
assert boot(note='fallback') == fallback
assert 'note=fallback' in (fallback / 'custom.log.pc_bootstrap.log').read_text(encoding='utf-8')
assert not (workspace / 'temp' / 'pc_bootstrap.log').exists()
# 所有目录不可写时保留原来的不抛异常约定。
os.environ['TEMP'] = str(workspace / 'debug')
assert boot() is None
print('ok')
"""
        with tempfile.TemporaryDirectory() as directory:
            result = isolated(code, self.script, directory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(sys.platform == 'win32', 'PC bootstrap runs on Windows')
    def test_rotated_logs_stay_in_module_directory_and_match_export_prefix(self):
        code = """
import logging.handlers
import runpy
import sys
from pathlib import Path
from unittest.mock import patch
script, workspace = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
mod = runpy.run_path(str(script), run_name='bootstrap_import_test')
globals_ = mod['_boot_log'].__globals__
globals_['AGENT_DIR'] = workspace / 'agent'
globals_['_run'] = lambda report: [report('rotation-probe-' + str(i) + '-' * 200) for i in range(5)]
handler_type = logging.handlers.RotatingFileHandler
def small_handler(*args, **kwargs):
    kwargs['maxBytes'] = 300
    return handler_type(*args, **kwargs)
with patch.object(logging.handlers, 'RotatingFileHandler', small_handler):
    globals_['main']()
directory = workspace / 'debug' / 'pc_bootstrap'
files = sorted(directory.iterdir())
assert [p.name for p in files] == [
    'custom.log.pc_bootstrap.log',
    'custom.log.pc_bootstrap.log.1',
    'custom.log.pc_bootstrap.log.2',
], files
assert all('rotation-probe-' in p.read_text(encoding='utf-8') for p in files)
assert not list((workspace / 'debug').glob('*.log'))
print('ok')
"""
        with tempfile.TemporaryDirectory() as directory:
            result = isolated(code, self.script, directory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
