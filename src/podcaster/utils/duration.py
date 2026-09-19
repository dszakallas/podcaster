"""Podcast-duration parsing and normalization."""

import pytimeparse2

_PRESET_DURATIONS: dict[str, str] = {
    "short": "10 minutes",
    "default": "20 minutes",
    "long": "30 minutes",
}


def parse_duration_minutes(duration: str) -> int | None:
    """Parse a human-readable duration into total minutes."""
    if not duration or not duration.strip():
        return None
    seconds = pytimeparse2.parse(duration, as_timedelta=False)
    if seconds is None:
        return None
    total_seconds = (
        seconds.total_seconds()
        if not isinstance(seconds, (int, float))
        else float(seconds)
    )
    return int(total_seconds // 60)


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
