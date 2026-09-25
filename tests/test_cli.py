"""Tests for the CLI interface."""
from __future__ import annotations

import pytest
from unittest.mock import patch
from io import StringIO

from reviewpilot.cli import build_parser, cmd_parse, cmd_analyze, cmd_check


SIMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index abc1234..def5678 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
+import os
 
 def main():
     pass
"""


class TestBuildParser:
    def test_creates_parser(self):
        parser = build_parser()
        assert parser is not None

    def test_review_command(self):
        parser = build_parser()
        args = parser.parse_args(["review", "test.diff"])
        assert args.command == "review"
        assert args.diff_file == "test.diff"

    def test_check_command(self):
        parser = build_parser()
        args = parser.parse_args(["check", "test.diff"])
        assert args.command == "check"

    def test_parse_command(self):
        parser = build_parser()
        args = parser.parse_args(["parse", "test.diff"])
        assert args.command == "parse"

    def test_analyze_command(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "src/main.py"])
        assert args.command == "analyze"
        assert args.source_file == "src/main.py"

    def test_review_with_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "review", "test.diff",
            "--deterministic-only",
            "--severity", "major",
            "--json",
        ])
        assert args.deterministic_only is True
        assert args.severity == "major"
        assert args.json_output is True

    def test_default_diff_is_stdin(self):
        parser = build_parser()
        args = parser.parse_args(["review"])
        assert args.diff_file == "-"

    def test_no_command_gives_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


class TestCmdParse:
    def test_parse_simple_diff(self, tmp_path):
        diff_file = tmp_path / "test.diff"
        diff_file.write_text(SIMPLE_DIFF)

        parser = build_parser()
        args = parser.parse_args(["parse", str(diff_file)])

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = cmd_parse(args)

        assert result == 0

    def test_parse_empty_diff(self, tmp_path):
        diff_file = tmp_path / "empty.diff"
        diff_file.write_text("")

        parser = build_parser()
        args = parser.parse_args(["parse", str(diff_file)])
        result = cmd_parse(args)
        assert result == 0


class TestCmdAnalyze:
    def test_analyze_python_file(self, tmp_path):
        source = tmp_path / "example.py"
        source.write_text("def hello():\n    return 'world'\n")

        parser = build_parser()
        args = parser.parse_args(["analyze", str(source)])
        result = cmd_analyze(args)
        assert result == 0

    def test_analyze_missing_file(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "/nonexistent/file.py"])
        result = cmd_analyze(args)
        assert result == 1

    def test_analyze_unsupported_file(self, tmp_path):
        data = tmp_path / "data.csv"
        data.write_text("a,b,c\n1,2,3\n")

        parser = build_parser()
        args = parser.parse_args(["analyze", str(data)])
        result = cmd_analyze(args)
        assert result == 1


class TestCmdCheck:
    def test_check_clean_diff(self, tmp_path):
        diff_file = tmp_path / "clean.diff"
        diff_file.write_text(SIMPLE_DIFF)

        # Create the source file that the diff references
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("import os\n\ndef main():\n    pass\n")

        parser = build_parser()
        args = parser.parse_args(["check", str(diff_file), "--repo", str(tmp_path)])
        result = cmd_check(args)
        assert result == 0  # No critical/major issues

    def test_check_with_secret(self, tmp_path):
        secret_diff = """\
diff --git a/config.py b/config.py
index abc..def 100644
--- a/config.py
+++ b/config.py
@@ -1,1 +1,2 @@
+API_KEY = "sk-1234567890abcdef1234567890abcdef"
 DEBUG = True
"""
        diff_file = tmp_path / "secret.diff"
        diff_file.write_text(secret_diff)
        (tmp_path / "config.py").write_text('API_KEY = "sk-1234567890abcdef1234567890abcdef"\nDEBUG = True\n')

        parser = build_parser()
        args = parser.parse_args(["check", str(diff_file), "--repo", str(tmp_path)])
        result = cmd_check(args)
        assert result == 1  # Critical issue found
