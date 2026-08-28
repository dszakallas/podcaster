"""Unit tests for pure utility functions in podcaster.audio_gen.core."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from notebooklm.rpc.types import AudioLength

from podcaster.audio_gen.core import (
    _poll_single_task,
    download_artifacts,
    duration_to_audio_length,
)
from podcaster.config import NotebookLMConfig
from podcaster.models import PodcastGenArtifact, TaskStatus


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


@pytest.mark.anyio
async def test_poll_single_task_waits_for_media_ready_status():
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        side_effect=AssertionError("polling must use poll_status")
    )
    client.artifacts.poll_status = AsyncMock(
        side_effect=[
            SimpleNamespace(
                status="in_progress",
                is_complete=False,
                is_failed=False,
                is_removed=False,
            ),
            SimpleNamespace(
                status="completed",
                is_complete=True,
                is_failed=False,
                is_removed=False,
            ),
        ]
    )

    with patch("podcaster.audio_gen.core.asyncio.sleep", new_callable=AsyncMock) as sleep:
        result = await _poll_single_task(client, "notebook", "artifact", "en")

    assert result["status"] == TaskStatus.COMPLETED
    assert client.artifacts.poll_status.await_count == 2
    sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_download_artifacts_propagates_download_error(tmp_path):
    client = MagicMock()
    client.artifacts.download_audio = AsyncMock(
        side_effect=RuntimeError("media URL is not ready")
    )
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=None)

    async def artifacts():
        yield PodcastGenArtifact(
            notebook_id="notebook",
            artifact_id="artifact",
            title="Episode",
            path="",
            filename="",
        )

    with patch(
        "podcaster.audio_gen.core.get_notebooklm_client", return_value=client_context
    ):
        with pytest.raises(RuntimeError, match="media URL is not ready"):
            async for _ in download_artifacts(
                artifacts(), str(tmp_path), NotebookLMConfig()
            ):
                pass
