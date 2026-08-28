"""Unit tests for Podcaster utility modules."""

import io
import logging

import pytest

from podcaster.utils.dbos import configure_dbos_logging
from podcaster.utils.duration import parse_duration_minutes, resolve_duration
from podcaster.utils.files import get_workflow_dir, sanitize
from podcaster.utils.logging import StructuredFormatter


def test_configure_dbos_logging_uses_application_formatter():
    dbos_logger = logging.getLogger("dbos")
    original_handlers = list(dbos_logger.handlers)
    original_level = dbos_logger.level
    original_propagate = dbos_logger.propagate
    root_logger = logging.getLogger()
    root_handler = logging.StreamHandler(io.StringIO())
    root_handler.setFormatter(
        StructuredFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    try:
        dbos_logger.addHandler(logging.NullHandler())
        dbos_logger.propagate = False
        dbos_logger.setLevel(logging.INFO)
        root_logger.addHandler(root_handler)

        configure_dbos_logging()
        dbos_logger.info("routine lifecycle message")
        dbos_logger.warning("database connection lost")

        assert dbos_logger.handlers == []
        assert dbos_logger.propagate is True
        assert dbos_logger.level == logging.WARNING
        log_output = root_handler.stream.getvalue()
        assert " - dbos - WARNING - database connection lost" in log_output
        assert "routine lifecycle message" not in log_output
    finally:
        root_logger.removeHandler(root_handler)
        dbos_logger.handlers.clear()
        dbos_logger.handlers.extend(original_handlers)
        dbos_logger.setLevel(original_level)
        dbos_logger.propagate = original_propagate

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
# get_workflow_dir
# ---------------------------------------------------------------------------


class TestGetWorkflowDir:
    def test_creates_workflow_dir(self, tmp_path):
        wf_dir = get_workflow_dir(tmp_path, "wf_123456")
        assert wf_dir.exists()
        assert wf_dir == tmp_path / "wf_123456"


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
