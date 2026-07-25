"""Unit tests for edge cases in branch_naming.py.

Covers: character sanitization, uniqueness by timestamp, prefix fix/auto-heal-.
Requirements: 9.1
"""
import re
from datetime import datetime, timezone, timedelta

import pytest

from services.self_healing_agent.branch_naming import build_fix_branch_name


class TestBranchNamingSanitization:
    """Tests that invalid git ref characters are sanitized."""

    def test_spaces_replaced(self):
        """Spaces in lambda name are replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("my function", ts)
        assert " " not in result

    def test_tildes_removed(self):
        """Tildes are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn~name", ts)
        assert "~" not in result

    def test_carets_removed(self):
        """Carets are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn^name", ts)
        assert "^" not in result

    def test_colons_removed(self):
        """Colons are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn:name", ts)
        assert ":" not in result

    def test_question_marks_removed(self):
        """Question marks are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn?name", ts)
        assert "?" not in result

    def test_asterisks_removed(self):
        """Asterisks are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn*name", ts)
        assert "*" not in result

    def test_brackets_removed(self):
        """Square brackets are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn[0]name", ts)
        assert "[" not in result
        assert "]" not in result

    def test_backslashes_removed(self):
        """Backslashes are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn\\name", ts)
        assert "\\" not in result

    def test_tabs_removed(self):
        """Tabs are removed/replaced."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn\tname", ts)
        assert "\t" not in result

    def test_consecutive_invalid_chars_collapse_to_single_dash(self):
        """Multiple consecutive invalid chars become a single dash."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn~~~name", ts)
        assert "---" not in result
        assert "--" not in result

    def test_all_invalid_chars_still_produces_valid_name(self):
        """Lambda name with only invalid characters uses 'unknown' fallback."""
        ts = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("~^:?*[]\\", ts)
        assert result.startswith("fix/auto-heal-")
        assert "unknown" in result


class TestBranchNamingPrefix:
    """Tests that the branch always has the correct prefix."""

    def test_prefix_with_normal_name(self):
        """Normal lambda name gets the prefix."""
        ts = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("create-task", ts)
        assert result.startswith("fix/auto-heal-")

    def test_prefix_with_empty_name(self):
        """Empty lambda name still gets the prefix."""
        ts = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("", ts)
        assert result.startswith("fix/auto-heal-")

    def test_prefix_with_long_name(self):
        """Long lambda name still gets the prefix."""
        ts = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("a" * 100, ts)
        assert result.startswith("fix/auto-heal-")

    def test_prefix_format_exact(self):
        """The prefix is exactly 'fix/auto-heal-' (with trailing dash)."""
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn", ts)
        assert result[:14] == "fix/auto-heal-"


class TestBranchNamingUniqueness:
    """Tests that different timestamps produce different branch names."""

    def test_one_second_apart(self):
        """Branches one second apart are different."""
        ts1 = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 7, 1, 12, 0, 1, tzinfo=timezone.utc)
        r1 = build_fix_branch_name("my-fn", ts1)
        r2 = build_fix_branch_name("my-fn", ts2)
        assert r1 != r2

    def test_same_timestamp_same_lambda_produces_same_name(self):
        """Deterministic: same inputs => same output."""
        ts = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        r1 = build_fix_branch_name("my-fn", ts)
        r2 = build_fix_branch_name("my-fn", ts)
        assert r1 == r2

    def test_same_timestamp_different_lambda_produces_different_name(self):
        """Different lambda names produce different branches."""
        ts = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        r1 = build_fix_branch_name("fn-a", ts)
        r2 = build_fix_branch_name("fn-b", ts)
        assert r1 != r2

    def test_different_days(self):
        """Different days produce different branch names."""
        ts1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        r1 = build_fix_branch_name("fn", ts1)
        r2 = build_fix_branch_name("fn", ts2)
        assert r1 != r2


class TestBranchNamingTimestamp:
    """Tests for timezone handling in the timestamp suffix."""

    def test_utc_timestamp_format(self):
        """UTC timestamp is in compact ISO format."""
        ts = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn", ts)
        assert "20251231T235959Z" in result

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (no tzinfo) is treated as UTC."""
        ts = datetime(2025, 6, 15, 10, 30, 0)
        result = build_fix_branch_name("fn", ts)
        assert "20250615T103000Z" in result

    def test_positive_offset_converted_to_utc(self):
        """Positive UTC offset is converted to UTC."""
        tz_plus_5 = timezone(timedelta(hours=5))
        ts = datetime(2025, 6, 15, 15, 0, 0, tzinfo=tz_plus_5)
        result = build_fix_branch_name("fn", ts)
        # 15:00+05:00 = 10:00 UTC
        assert "20250615T100000Z" in result

    def test_negative_offset_converted_to_utc(self):
        """Negative UTC offset is converted to UTC."""
        tz_minus_8 = timezone(timedelta(hours=-8))
        ts = datetime(2025, 6, 15, 2, 0, 0, tzinfo=tz_minus_8)
        result = build_fix_branch_name("fn", ts)
        # 02:00-08:00 = 10:00 UTC
        assert "20250615T100000Z" in result
