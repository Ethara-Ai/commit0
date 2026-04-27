"""Exhaustive unit tests for commit0.harness.health_check_rust."""

import subprocess
import logging
import pytest
from unittest.mock import patch, MagicMock

MODULE = "commit0.harness.health_check_rust"


# ===== _RUST_TOOLS constant =====
from commit0.harness.health_check_rust import _RUST_TOOLS


class TestRustToolsConstant:
    def test_is_list(self):
        assert isinstance(_RUST_TOOLS, list)

    def test_has_five_tools(self):
        assert len(_RUST_TOOLS) == 5

    def test_all_tuples_of_three(self):
        for tool in _RUST_TOOLS:
            assert len(tool) == 3

    def test_rustc_present(self):
        names = [t[0] for t in _RUST_TOOLS]
        assert "rustc" in names

    def test_cargo_present(self):
        names = [t[0] for t in _RUST_TOOLS]
        assert "cargo" in names

    def test_nextest_present(self):
        names = [t[0] for t in _RUST_TOOLS]
        assert "cargo-nextest" in names

    def test_clippy_present(self):
        names = [t[0] for t in _RUST_TOOLS]
        assert "clippy" in names

    def test_rustfmt_present(self):
        names = [t[0] for t in _RUST_TOOLS]
        assert "rustfmt" in names

    def test_all_hints_are_strings(self):
        for _, _, hint in _RUST_TOOLS:
            assert isinstance(hint, str)
            assert len(hint) > 0

    def test_all_commands_are_lists(self):
        for _, cmd, _ in _RUST_TOOLS:
            assert isinstance(cmd, list)
            assert len(cmd) > 0

    def test_rustc_command(self):
        for name, cmd, _ in _RUST_TOOLS:
            if name == "rustc":
                assert cmd == ["rustc", "--version"]

    def test_cargo_command(self):
        for name, cmd, _ in _RUST_TOOLS:
            if name == "cargo":
                assert cmd == ["cargo", "--version"]

    def test_nextest_command(self):
        for name, cmd, _ in _RUST_TOOLS:
            if name == "cargo-nextest":
                assert cmd == ["cargo", "nextest", "--version"]

    def test_clippy_command(self):
        for name, cmd, _ in _RUST_TOOLS:
            if name == "clippy":
                assert cmd == ["cargo", "clippy", "--version"]

    def test_rustfmt_command(self):
        for name, cmd, _ in _RUST_TOOLS:
            if name == "rustfmt":
                assert cmd == ["rustfmt", "--version"]

    def test_hints_contain_install_info(self):
        for _, _, hint in _RUST_TOOLS:
            assert "rustup" in hint or "cargo" in hint or "https" in hint


# ===== _check_tool =====
from commit0.harness.health_check_rust import _check_tool


class TestCheckTool:
    def test_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "rustc 1.75.0 (abc 2024-01-01)\n"
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            ok, name, detail = _check_tool("rustc", ["rustc", "--version"], "hint")
        assert ok is True
        assert name == "rustc"
        assert "rustc 1.75.0" in detail

    def test_nonzero_returncode(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error: not found\n"
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            ok, name, detail = _check_tool("cargo", ["cargo", "--version"], "install cargo")
        assert ok is False
        assert name == "cargo"
        assert "FAIL" in detail
        assert "install cargo" in detail

    def test_file_not_found(self):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError()):
            ok, name, detail = _check_tool("rustfmt", ["rustfmt"], "hint")
        assert ok is False
        assert "not found" in detail

    def test_timeout(self):
        with patch(f"{MODULE}.subprocess.run", side_effect=subprocess.TimeoutExpired(["x"], 30)):
            ok, name, detail = _check_tool("rustc", ["rustc"], "hint")
        assert ok is False
        assert "timed out" in detail

    def test_generic_exception(self):
        with patch(f"{MODULE}.subprocess.run", side_effect=PermissionError("denied")):
            ok, name, detail = _check_tool("cargo", ["cargo"], "hint")
        assert ok is False
        assert "FAIL" in detail
        assert "denied" in detail

    def test_empty_stderr_on_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = ""
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            ok, name, detail = _check_tool("x", ["x"], "fix it")
        assert ok is False
        assert "unknown error" in detail

    def test_multiline_stdout_takes_first(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "cargo 1.75.0\nextra info\n"
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            ok, name, detail = _check_tool("cargo", ["cargo"], "hint")
        assert ok is True
        assert "cargo 1.75.0" in detail
        assert "extra info" not in detail

    def test_hint_included_on_failure(self):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError()):
            ok, name, detail = _check_tool("x", ["x"], "cargo install x")
        assert "cargo install x" in detail

    def test_returns_tuple_of_three(self):
        mock_result = MagicMock(returncode=0, stdout="v1\n")
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            result = _check_tool("x", ["x"], "h")
        assert isinstance(result, tuple)
        assert len(result) == 3

    @pytest.mark.parametrize("tool_name", ["rustc", "cargo", "cargo-nextest", "clippy", "rustfmt"])
    def test_preserves_tool_name(self, tool_name):
        mock_result = MagicMock(returncode=0, stdout="v1\n")
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            _, name, _ = _check_tool(tool_name, ["x"], "h")
        assert name == tool_name

    def test_debug_logging_on_failure(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=MODULE):
            with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError()):
                _check_tool("rustc", ["rustc"], "hint")
        assert "not found" in caplog.text.lower()

    def test_debug_logging_on_timeout(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=MODULE):
            with patch(f"{MODULE}.subprocess.run", side_effect=subprocess.TimeoutExpired(["x"], 30)):
                _check_tool("cargo", ["cargo"], "hint")
        assert "timed out" in caplog.text.lower()

    def test_debug_logging_on_nonzero(self, caplog):
        mock_result = MagicMock(returncode=1, stderr="bad")
        with caplog.at_level(logging.DEBUG, logger=MODULE):
            with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
                _check_tool("cargo", ["cargo"], "hint")
        assert "failed" in caplog.text.lower()


# ===== main =====
from commit0.harness.health_check_rust import main


class TestMain:
    def _mock_all_pass(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "tool 1.0.0\n"
        return mock_result

    def test_all_pass_returns_true(self):
        with patch(f"{MODULE}.subprocess.run", return_value=self._mock_all_pass()):
            assert main() is True

    def test_one_fails_returns_false(self):
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 3:
                raise FileNotFoundError()
            m = MagicMock(returncode=0, stdout="v1\n")
            return m
        with patch(f"{MODULE}.subprocess.run", side_effect=side_effect):
            assert main() is False

    def test_all_fail_returns_false(self):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError()):
            assert main() is False

    def test_prints_header(self, capsys):
        with patch(f"{MODULE}.subprocess.run", return_value=self._mock_all_pass()):
            main()
        captured = capsys.readouterr()
        assert "Rust Health Check" in captured.out

    def test_prints_pass_for_passing_tools(self, capsys):
        with patch(f"{MODULE}.subprocess.run", return_value=self._mock_all_pass()):
            main()
        captured = capsys.readouterr()
        assert "PASS" in captured.out

    def test_prints_fail_for_failing_tools(self, capsys):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError()):
            main()
        captured = capsys.readouterr()
        assert "FAIL" in captured.out

    def test_checks_all_five_tools(self):
        with patch(f"{MODULE}.subprocess.run", return_value=self._mock_all_pass()) as mock_run:
            main()
        assert mock_run.call_count == 5

    def test_accepts_base_dir_arg(self):
        with patch(f"{MODULE}.subprocess.run", return_value=self._mock_all_pass()):
            result = main(base_dir="/some/path")
        assert result is True

    def test_info_log_on_all_pass(self, caplog):
        with caplog.at_level(logging.INFO, logger=MODULE):
            with patch(f"{MODULE}.subprocess.run", return_value=self._mock_all_pass()):
                main()
        assert "all tools available" in caplog.text

    def test_warning_log_on_failure(self, caplog):
        with caplog.at_level(logging.WARNING, logger=MODULE):
            with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError()):
                main()
        assert "missing or broken" in caplog.text

    def test_version_extracted_from_output(self, capsys):
        mock_result = MagicMock(returncode=0, stdout="rustc 1.80.1 (2024-08-01)\n")
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            main()
        captured = capsys.readouterr()
        assert "1.80.1" in captured.out

    def test_no_version_number_in_output(self, capsys):
        mock_result = MagicMock(returncode=0, stdout="toolname\n")
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            main()
        # Should still print PASS without version
        captured = capsys.readouterr()
        assert "PASS" in captured.out

    def test_tool_order_preserved(self, capsys):
        mock_result = MagicMock(returncode=0, stdout="v1.0\n")
        with patch(f"{MODULE}.subprocess.run", return_value=mock_result):
            main()
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # First line is header, then 5 tool lines
        assert len(lines) >= 6

    @pytest.mark.parametrize("failing_index", [0, 1, 2, 3, 4])
    def test_single_failure_at_each_position(self, failing_index):
        call_count = [0]
        def side_effect(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == failing_index:
                raise FileNotFoundError()
            return MagicMock(returncode=0, stdout="v1\n")
        with patch(f"{MODULE}.subprocess.run", side_effect=side_effect):
            assert main() is False


# ===== Module exports =====
class TestModuleExports:
    def test_all_exports(self):
        import commit0.harness.health_check_rust as mod
        assert "main" in mod.__all__
