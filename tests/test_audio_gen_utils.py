"""Unit tests for pure utility functions in podcaster.audio_gen.core."""

from notebooklm.rpc.types import AudioLength

from podcaster.audio_gen.core import duration_to_audio_length


class TestDurationToAudioLength:
    def test_short_at_boundary(self):
        assert duration_to_audio_length("12 minutes") == AudioLength.SHORT

    def test_short_below_boundary(self):
        assert duration_to_audio_length("5 minutes") == AudioLength.SHORT

    def test_short_at_zero(self):
        assert duration_to_audio_length("0 minutes") == AudioLength.SHORT

    def test_default_at_13(self):
        assert duration_to_audio_length("13 minutes") == AudioLength.DEFAULT

    def test_default_at_boundary(self):
        # Code checks <= 24, docstring says <= 25
        assert duration_to_audio_length("24 minutes") == AudioLength.DEFAULT

    def test_long_at_25(self):
        # Code checks <= 24 for DEFAULT, so 25 is LONG
        assert duration_to_audio_length("25 minutes") == AudioLength.LONG

    def test_long_at_30(self):
        assert duration_to_audio_length("30 minutes") == AudioLength.LONG

    def test_long_at_1_hour(self):
        assert duration_to_audio_length("1 hour 0 minutes") == AudioLength.LONG

    def test_hours_only_unparseable(self):
        # "1 hour" without minutes fails the regex → defaults to DEFAULT
        assert duration_to_audio_length("1 hour") == AudioLength.DEFAULT

    def test_unparseable_defaults(self):
        assert duration_to_audio_length("nonsense") == AudioLength.DEFAULT

    def test_empty_string_defaults(self):
        assert duration_to_audio_length("") == AudioLength.DEFAULT
