"""Offline installation selection and actual launch-adapter wiring tests."""

import ntpath
import os
import stat
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from startup import pc_install, win32
from startup.common import Budget, Cancelled, PreparationError


def windows_error(code):
    kind = FileNotFoundError if code in (53, 67) else OSError
    error = kind(f"Windows error {code}")
    error.winerror = code
    return error


def record(number="10000002", **changes):
    values = {"path": (rf"E:\Neowiz\BrownDust2_{number}", 1),
              "execute": ("BrownDust II.exe", 1)}
    values.update(changes)
    return values


class Key:
    def __init__(self, registry, name):
        self.registry, self.name = registry, name

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.registry.closed.append(self.name)


class Registry:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_CLASSES_ROOT = "HKCR"
    KEY_ENUMERATE_SUB_KEYS = 8
    KEY_QUERY_VALUE = 1
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self, records=None):
        self.records = {"10000002": record()} if records is None else records
        self.command = (r'"C:\ProgramData\Neowiz\Browndust2Starter.exe" "%1"', 1)
        self.events, self.closed = [], []
        self.on_event = lambda event: None
        self.missing = False

    def event(self, *event):
        self.events.append(event)
        self.on_event(event)

    def OpenKey(self, root, path, reserved, access):
        parent = root.name if isinstance(root, Key) else root
        self.event("open", parent, path, access)
        if root == self.HKEY_CURRENT_USER:
            assert path == pc_install.INSTALL_KEY and access == self.KEY_ENUMERATE_SUB_KEYS
            if self.missing:
                raise FileNotFoundError(path)
            return Key(self, "install")
        if root == self.HKEY_CLASSES_ROOT:
            assert path == r"browndust2\shell\open\command" and access == self.KEY_QUERY_VALUE
            return Key(self, "protocol")
        assert parent == "install" and access == self.KEY_QUERY_VALUE
        return Key(self, path)

    def EnumKey(self, key, index):
        self.event("enum", key.name, index)
        names = list(self.records)
        if index >= len(names):
            raise windows_error(259)
        return names[index]

    def QueryInfoKey(self, key):
        raise AssertionError("The root only has enumeration rights")

    def QueryValueEx(self, key, name):
        self.event("query", key.name, name)
        if key.name == "protocol":
            return self.command
        try:
            return self.records[key.name][name]
        except KeyError:
            raise FileNotFoundError(name)

    def ExpandEnvironmentStrings(self, value):
        return value.replace("%BD2_ROOT%", r"E:\Neowiz")


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.stat_calls = []
        self.on_stat = lambda path: None

    def stat_file(self, path):
        self.stat_calls.append(path)
        self.on_stat(path)
        return SimpleNamespace(st_mode=stat.S_IFREG)

    def discover(self, check=lambda: None):
        return pc_install.discover_installations(
            registry=self.registry, stat_file=self.stat_file, check=check)

    def launch(self, check=lambda: None):
        # Invoke the production adapter without constructing a native controller.
        # Every registry, filesystem and launch operation is replaced locally.
        with patch.dict(sys.modules, {"winreg": self.registry}), \
                patch.object(os, "stat", self.stat_file), \
                patch.object(os, "startfile", create=True) as start:
            result = win32.WindowsAPI.launch(None, check=check)
        return result, start

    def assert_no_launch(self, expected=PreparationError, check=lambda: None):
        with patch.dict(sys.modules, {"winreg": self.registry}), \
                patch.object(os, "stat", self.stat_file), \
                patch.object(os, "startfile", create=True) as start:
            with self.assertRaises(expected) as caught:
                win32.WindowsAPI.launch(None, check=check)
            start.assert_not_called()
        return str(caught.exception)

    def test_actual_adapter_uses_each_selected_number(self):
        for number in ("10000001", "10000002", "987654321", "0007"):
            with self.subTest(number=number):
                self.registry = Registry({number: record(number)})
                selected, start = self.launch()
                start.assert_called_once_with(f"browndust2:games/{number}?usn=0")
                self.assertEqual(selected.installation.number, number)
                self.assertIn("唯一有效安装", selected.diagnostics[-1])
                self.assertEqual(self.registry.closed, [number, "install", "protocol"])

    def test_invalid_numbers_are_not_unicode_digits(self):
        for number in ("", "１００００００２", "²", "12\n", "-1", "1?x=2"):
            with self.subTest(number=number):
                self.registry = Registry({number: record(number)})
                self.assertIn("半角数字", self.assert_no_launch())

    def test_stale_file_is_excluded_and_other_installation_is_used(self):
        self.registry = Registry({"10000001": record("10000001"), "10000002": record()})
        def missing(path):
            if "10000001" in path:
                raise FileNotFoundError(path)
        self.on_stat = missing
        selected, start = self.launch()
        self.assertIn("文件不存在", selected.diagnostics[0])
        start.assert_called_once_with("browndust2:games/10000002?usn=0")

    def test_zero_and_multiple_candidates_never_launch(self):
        for records in ({}, {"1": record("1"), "2": record("2")},
                        {"1": record(), "2": record()}):
            with self.subTest(records=records):
                self.registry = Registry(records)
                reason = self.assert_no_launch()
                self.assertIn("多个有效安装" if records else "未找到有效安装", reason)

    def test_missing_root_is_distinct_from_denied_root(self):
        self.registry.missing = True
        self.assertIn("没有 Browndust2Starter 安装记录", self.assert_no_launch())
        def denied(event):
            if event[0] == "open":
                raise PermissionError("access denied")
        self.registry.on_event = denied
        reason = self.assert_no_launch()
        self.assertIn("无法读取", reason)
        self.assertIn("access denied", reason)

    def test_invalid_required_fields_and_paths(self):
        variants = [
            {"path": None}, {"execute": None}, {"path": (123, 4)},
            {"path": ("E:\\game", 7)}, {"path": ("", 1)}, {"path": ("E:\\bad\x00", 1)},
            {"path": (r"relative\game", 1)}, {"path": (r"\Neowiz\game", 1)},
            {"path": (r"E:relative", 1)}, {"path": (r"\\server", 1)},
            {"path": (r"\\.\device", 1)},
            {"execute": ("Starter.exe", 1)}, {"execute": (r"bin\BrownDust II.exe", 1)},
            {"execute": ('"BrownDust II.exe"', 1)}, {"execute": ("BrownDust II.exe --arg", 1)},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                values = record(**changes)
                values = {key: value for key, value in values.items() if value is not None}
                self.registry = Registry({"10000002": values})
                self.assertIn("排除安装", self.assert_no_launch())

    def test_complete_paths_and_case_insensitive_filename(self):
        paths = [r"E:\Neowiz Game", "E:\\", r"\\server\share\game", r"\\server\share",
                 "E:/Neowiz/game", r"\\?\E:\game", r"\\?\UNC\server\share\game"]
        for directory in paths:
            with self.subTest(directory=directory):
                self.registry = Registry({"10000002": record(
                    path=(directory, 1), execute=("BROWNDUST II.EXE", 1))})
                selected = pc_install.select_installation(self.discover())
                self.assertEqual(selected.installation.path, ntpath.join(directory, "BROWNDUST II.EXE"))

    def test_expandable_string_and_optional_metadata(self):
        self.registry = Registry({"10000002": record(
            path=(r"%BD2_ROOT%\game", 2), version=(42, 4), environment=("LIVE", 1))})
        selected = pc_install.select_installation(self.discover())
        self.assertEqual(selected.installation.directory, r"E:\Neowiz\game")
        self.assertIn("environment='LIVE'", selected.diagnostics[0])
        self.assertIn("version=None", selected.diagnostics[0])

    def test_directory_is_not_a_game_file(self):
        with patch.object(self, "stat_file", return_value=SimpleNamespace(st_mode=stat.S_IFDIR)):
            self.assertIn("不是普通文件", self.assert_no_launch())

    def test_partial_enumeration_or_disappearing_key_is_not_unique(self):
        for stage in ("enum", "open", "query"):
            with self.subTest(stage=stage):
                self.registry = Registry({"1": record("1"), "2": record("2")})
                def fail(event):
                    if ((stage == "enum" and event == ("enum", "install", 1))
                            or (stage == "open" and event[:3] == ("open", "install", "2"))
                            or (stage == "query" and event == ("query", "2", "path"))):
                        raise FileNotFoundError("subkey vanished") if stage == "open" else windows_error(5)
                self.registry.on_event = fail
                reason = self.assert_no_launch()
                self.assertIn("读取不完整", reason)
                self.assertIn("有效安装 1", reason)
                self.assertIn("install", self.registry.closed)

    def test_unknown_file_error_is_not_an_invalid_record_to_skip(self):
        self.registry = Registry({"1": record("1"), "2": record("2")})
        for error in (PermissionError("denied"), windows_error(53), windows_error(67)):
            with self.subTest(error=error):
                def fail(path):
                    if "_2" in path:
                        raise error
                self.on_stat = fail
                self.assertIn("读取不完整", self.assert_no_launch())

    def test_optional_metadata_access_error_still_fails_discovery(self):
        def fail(event):
            if event == ("query", "10000002", "version"):
                raise PermissionError("version denied")
        self.registry.on_event = fail
        self.assertIn("version denied", self.assert_no_launch())

    def test_missing_or_invalid_protocol_never_launches(self):
        for command in ((123, 4), ('"C:\\Other.exe" "%1"', 1), ("", 1)):
            with self.subTest(command=command):
                self.registry.command = command
                self.assertIn("校验官方启动入口失败", self.assert_no_launch())
        def fail(event):
            if event[:2] == ("open", "HKCR"):
                raise FileNotFoundError("protocol missing")
        self.registry.on_event = fail
        self.assertIn("protocol missing", self.assert_no_launch())

    def test_selected_file_is_rechecked_immediately_before_request(self):
        calls = []
        def disappear(path):
            if path.endswith("BrownDust II.exe"):
                calls.append(path)
                if len(calls) == 2:
                    raise FileNotFoundError(path)
        self.on_stat = disappear
        reason = self.assert_no_launch()
        self.assertIn("启动前复核游戏文件失败", reason)
        self.assertEqual(len(calls), 2)

    def test_request_error_preserves_selection_and_is_not_retried(self):
        with patch.dict(sys.modules, {"winreg": self.registry}), \
                patch.object(os, "stat", self.stat_file), \
                patch.object(os, "startfile", side_effect=OSError("shell failed"), create=True) as start:
            with self.assertRaisesRegex(PreparationError, "提交官方启动请求失败.*10000002.*shell failed"):
                win32.WindowsAPI.launch(None, check=lambda: None)
            start.assert_called_once()

    def test_expired_budget_or_cancellation_at_each_stage_never_launches(self):
        for cancel in (False, True):
            for stage in ("enum", "query", "protocol", "recheck"):
                with self.subTest(cancel=cancel, stage=stage):
                    self.registry = Registry()
                    state = {"stop": False, "now": 0.0, "game_stats": 0}
                    def stop():
                        state.update(stop=True, now=300.0)
                    def on_event(event):
                        if ((stage == "enum" and event[0] == "enum")
                                or (stage == "query" and event == ("query", "10000002", "path"))
                                or (stage == "protocol" and event == ("query", "protocol", ""))):
                            stop()
                    def on_stat(path):
                        if path.endswith("BrownDust II.exe"):
                            state["game_stats"] += 1
                            if stage == "recheck" and state["game_stats"] == 2:
                                stop()
                    self.registry.on_event = on_event
                    self.on_stat = on_stat
                    budget = Budget(lambda _: None, lambda: cancel and state["stop"],
                                    clock=lambda: state["now"])
                    self.assert_no_launch(Cancelled if cancel else PreparationError, check=budget.check)


if __name__ == "__main__":
    unittest.main(verbosity=2)
