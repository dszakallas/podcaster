"""Unit tests for pure utility functions in podcaster.utils."""

from datetime import datetime

import pytest

from podcaster.utils import (
    find_notebook_dir,
    get_notebook_dir_name,
    parse_duration_minutes,
    resolve_duration,
    sanitize,
)

# ---------------------------------------------------------------------------
# sanitize
# ---------------------------------------------------------------------------


class TestSanitize:
    def test_alphanumeric_passthrough(self):
        assert sanitize("abc123") == "abc123"

    def test_spaces_to_underscores(self):
        assert sanitize("hello world") == "hello_world"

    def test_special_chars_to_underscores(self):
        assert sanitize("a-b.c!d@e") == "a_b_c_d_e"

    def test_empty_string(self):
        assert sanitize("") == ""

    def test_all_special_chars(self):
        assert sanitize("!@#$%") == "_____"

    def test_unicode_alphanumeric(self):
        # Unicode letters (accented) are alphanumeric in Python's str.isalnum()
        result = sanitize("café")
        assert result == "café"

    def test_unicode_cjk(self):
        # CJK characters are alphanumeric
        result = sanitize("日本語")
        assert result == "日本語"

    def test_mixed_unicode_special(self):
        result = sanitize("café latte!")
        assert result == "café_latte_"

    def test_consecutive_specials(self):
        assert sanitize("a---b") == "a___b"


# ---------------------------------------------------------------------------
# get_notebook_dir_name
# ---------------------------------------------------------------------------


class TestGetNotebookDirName:
    def test_without_date(self):
        result = get_notebook_dir_name("My Podcast", "abc-123")
        assert result == "My_Podcast [nlm_abc-123]"

    def test_with_date(self):
        dt = datetime(2024, 3, 15, 10, 30)
        result = get_notebook_dir_name("My Podcast", "abc-123", created_at=dt)
        assert result == "2024-03-15 - My_Podcast [nlm_abc-123]"

    def test_special_chars_in_title(self):
        result = get_notebook_dir_name("Hello/World!", "id1")
        assert result == "Hello_World_ [nlm_id1]"

    def test_empty_title(self):
        result = get_notebook_dir_name("", "id1")
        assert result == " [nlm_id1]"

    def test_none_date_omits_prefix(self):
        result = get_notebook_dir_name("Title", "id1", created_at=None)
        assert not result.startswith("0000")
        assert result == "Title [nlm_id1]"


# ---------------------------------------------------------------------------
# find_notebook_dir
# ---------------------------------------------------------------------------


class TestFindNotebookDir:
    def test_finds_matching_dir(self, tmp_path):
        (tmp_path / "2024-01-01 - Test [nlm_abc-123]").mkdir()
        result = find_notebook_dir(str(tmp_path), "abc-123")
        assert result == "2024-01-01 - Test [nlm_abc-123]"

    def test_returns_none_when_not_found(self, tmp_path):
        (tmp_path / "other_dir").mkdir()
        assert find_notebook_dir(str(tmp_path), "nonexistent") is None

    def test_returns_none_for_nonexistent_base(self):
        assert find_notebook_dir("/nonexistent/path", "id") is None

    def test_ignores_files_with_suffix(self, tmp_path):
        # Create a file (not directory) with the suffix
        (tmp_path / "Test [nlm_abc-123]").write_text("not a dir")
        assert find_notebook_dir(str(tmp_path), "abc-123") is None

    def test_multiple_matches_returns_first(self, tmp_path):
        (tmp_path / "First [nlm_abc-123]").mkdir()
        (tmp_path / "Second [nlm_abc-123]").mkdir()
        result = find_notebook_dir(str(tmp_path), "abc-123")
        assert result is not None
        assert "[nlm_abc-123]" in result


# ---------------------------------------------------------------------------
# parse_duration_minutes
# ---------------------------------------------------------------------------


class TestParseDurationMinutes:
    def test_minutes_only(self):
        assert parse_duration_minutes("10 minutes") == 10

    def test_minutes_short(self):
        assert parse_duration_minutes("45m") == 45

    def test_minutes_min(self):
        assert parse_duration_minutes("30min") == 30

    def test_hours_and_minutes(self):
        assert parse_duration_minutes("1 hour 30 minutes") == 90

    def test_hours_short(self):
        assert parse_duration_minutes("2h 15m") == 135

    def test_zero_minutes(self):
        assert parse_duration_minutes("0 minutes") == 0

    def test_hours_only_no_minutes(self):
        # The regex requires minutes
        assert parse_duration_minutes("1 hour") is None

    def test_empty_string(self):
        assert parse_duration_minutes("") is None

    def test_invalid_string(self):
        assert parse_duration_minutes("not a duration") is None

    def test_case_insensitive(self):
        assert parse_duration_minutes("1 Hour 30 Minutes") == 90

    def test_whitespace_padded(self):
        assert parse_duration_minutes("  45 minutes  ") == 45

    def test_no_spaces(self):
        assert parse_duration_minutes("1hour30minutes") == 90

    def test_plural_hours(self):
        assert parse_duration_minutes("2 hours 5 minutes") == 125

    def test_just_minutes_word(self):
        assert parse_duration_minutes("minutes") is None

    def test_large_value(self):
        assert parse_duration_minutes("100 hours 30 minutes") == 6030


# ---------------------------------------------------------------------------
# resolve_duration
# ---------------------------------------------------------------------------


class TestResolveDuration:
    def test_short_preset(self):
        assert resolve_duration("short") == "10 minutes"

    def test_default_preset(self):
        assert resolve_duration("default") == "20 minutes"

    def test_long_preset(self):
        assert resolve_duration("long") == "30 minutes"

    def test_custom_duration_passthrough(self):
        assert resolve_duration("23 minutes") == "23 minutes"

    def test_custom_duration_with_hours(self):
        assert resolve_duration("1 hour 5 minutes") == "1 hour 5 minutes"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid length"):
            resolve_duration("nonsense")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            resolve_duration("")
