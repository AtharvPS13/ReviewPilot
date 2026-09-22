"""Tests for deterministic pre-filter checks."""
from __future__ import annotations

import pytest

from reviewpilot.analysis.deterministic import run_deterministic_checks
from reviewpilot.review.models import Category, Severity


class TestHardcodedSecrets:
    def test_detects_api_key(self):
        changed = {5: 'API_KEY = "sk-1234567890abcdef1234567890abcdef"'}
        issues = run_deterministic_checks("config.py", changed)
        assert any(i.category == Category.SECURITY for i in issues)

    def test_detects_password(self):
        changed = {10: 'password = "super_secret_pass_123"'}
        issues = run_deterministic_checks("auth.py", changed)
        assert any("password" in i.explanation.lower() for i in issues)

    def test_ignores_placeholder_values(self):
        changed = {5: 'API_KEY = "test_placeholder_key_12345678"'}
        issues = run_deterministic_checks("config.py", changed)
        secret_issues = [i for i in issues if i.category == Category.SECURITY]
        assert len(secret_issues) == 0

    def test_ignores_example_values(self):
        changed = {5: 'api_key = "example_key_1234567890123456"'}
        issues = run_deterministic_checks("config.py", changed)
        secret_issues = [i for i in issues if "Hardcoded" in i.explanation]
        assert len(secret_issues) == 0


class TestDangerousFunctions:
    def test_detects_eval_in_python(self):
        changed = {8: '    result = eval(user_input)'}
        issues = run_deterministic_checks("handler.py", changed)
        assert any("eval" in i.explanation.lower() for i in issues)

    def test_detects_exec_in_python(self):
        changed = {12: '    exec(code_string)'}
        issues = run_deterministic_checks("runner.py", changed)
        assert any("exec" in i.explanation.lower() for i in issues)

    def test_eval_not_flagged_in_js(self):
        changed = {8: '    result = eval(user_input)'}
        issues = run_deterministic_checks("handler.js", changed)
        eval_issues = [i for i in issues if "eval" in i.explanation.lower()]
        assert len(eval_issues) == 0  # Python-only rule


class TestSQLInjection:
    def test_detects_fstring_in_execute(self):
        changed = {15: '    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")'}
        issues = run_deterministic_checks("db.py", changed)
        assert any(i.category == Category.SECURITY for i in issues)

    def test_detects_format_in_execute(self):
        changed = {15: '    cursor.execute("SELECT * FROM users WHERE id = %s" % user_id)'}
        issues = run_deterministic_checks("db.py", changed)
        assert any("SQL" in i.explanation for i in issues)


class TestErrorHandling:
    def test_detects_bare_except(self):
        changed = {20: "except:"}
        issues = run_deterministic_checks("handler.py", changed)
        assert any("except" in i.explanation.lower() for i in issues)

    def test_detects_empty_catch_js(self):
        changed = {20: "    } catch (e) { }"}
        issues = run_deterministic_checks("handler.ts", changed)
        assert any("catch" in i.explanation.lower() for i in issues)


class TestTodoComments:
    def test_detects_todo(self):
        changed = {5: "    # TODO: fix this later"}
        issues = run_deterministic_checks("utils.py", changed)
        assert any("TODO" in i.explanation for i in issues)

    def test_detects_fixme(self):
        changed = {5: "    # FIXME: broken edge case"}
        issues = run_deterministic_checks("utils.py", changed)
        assert any("TODO" in i.explanation or "FIXME" in i.explanation for i in issues)


class TestSeverityFiltering:
    def test_min_severity_filters_low(self):
        changed = {5: "    # TODO: cleanup"}
        issues = run_deterministic_checks("utils.py", changed, min_severity=Severity.MAJOR)
        todo_issues = [i for i in issues if "TODO" in i.explanation]
        assert len(todo_issues) == 0  # NITPICK severity filtered out

    def test_min_severity_keeps_critical(self):
        changed = {5: '    result = eval(user_input)'}
        issues = run_deterministic_checks("handler.py", changed, min_severity=Severity.MAJOR)
        assert len(issues) > 0  # CRITICAL severity passes filter


class TestFileTypeFiltering:
    def test_python_rules_not_applied_to_go(self):
        changed = {5: '    result = eval(user_input)'}
        issues = run_deterministic_checks("main.go", changed)
        eval_issues = [i for i in issues if "eval" in i.explanation.lower()]
        assert len(eval_issues) == 0

    def test_universal_rules_apply_everywhere(self):
        changed = {5: "    # TODO: fix"}
        py_issues = run_deterministic_checks("main.py", changed)
        go_issues = run_deterministic_checks("main.go", changed)
        assert len(py_issues) > 0
        assert len(go_issues) > 0
