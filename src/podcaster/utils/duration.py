"""Podcast-duration parsing and normalization."""

import re

_PRESET_DURATIONS: dict[str, str] = {
    "short": "10 minutes",
    "default": "20 minutes",
    "long": "30 minutes",
}
_DURATION_RE = r"""(?x)
    ^\s*
    (?:(\d+)\s*h(?:ours?)?)?\s*
    (?:(\d+)\s*m(?:in(?:utes?)?)?)
    \s*$
"""


def parse_duration_minutes(duration: str) -> int | None:
    """Parse a human-readable duration into total minutes."""
    match = re.match(_DURATION_RE, duration.strip(), re.VERBOSE | re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def resolve_duration(length: str) -> str:
    """Normalize a preset or human-readable duration string."""
    if length in _PRESET_DURATIONS:
        return _PRESET_DURATIONS[length]
    if parse_duration_minutes(length) is not None:
        return length
    raise ValueError(
        f"Invalid length {length!r}: expected a preset ('short', 'default', 'long') "
        "or a duration string (e.g. '23 minutes', '1 hour 10 minutes')."
    )
