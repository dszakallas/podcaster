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

    def test_hours_only_parsed(self):
        # "1 hour" parses to 60 minutes → maps to LONG
        assert duration_to_audio_length("1 hour") == AudioLength.LONG

    def test_unparseable_defaults(self):
        assert duration_to_audio_length("nonsense") == AudioLength.DEFAULT

    def test_empty_string_defaults(self):
        assert duration_to_audio_length("") == AudioLength.DEFAULT


@pytest.mark.anyio
async def test_poll_single_task_waits_for_media_ready_status():
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[SimpleNamespace(id="artifact", title="Episode", created_at=None)]
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

    with patch(
        "podcaster.audio_gen.core.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        result = await _poll_single_task(client, "notebook", "artifact", "en")

    assert result["status"] == TaskStatus.COMPLETED
    assert client.artifacts.poll_status.await_count == 2
    client.artifacts.list.assert_awaited_once_with("notebook")
    sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_poll_single_task_retains_completed_artifact_title():
    client = MagicMock()
    client.artifacts.poll_status = AsyncMock(
        return_value=SimpleNamespace(
            status="completed",
            is_complete=True,
            is_failed=False,
            is_removed=False,
        )
    )
    client.artifacts.list = AsyncMock(
        return_value=[
            SimpleNamespace(
                id="artifact", title="A descriptive episode", created_at=None
            )
        ]
    )

    result = await _poll_single_task(client, "notebook", "artifact", "en")

    assert result["title"] == "A descriptive episode"


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

    with (
        patch(
            "podcaster.audio_gen.core.get_notebooklm_client",
            return_value=client_context,
        ),
        pytest.raises(RuntimeError, match="media URL is not ready"),
    ):
        async for _ in download_artifacts(
            artifacts(), str(tmp_path), NotebookLMConfig()
        ):
            pass


@pytest.mark.anyio
async def test_poll_single_task_fallback_on_timeout_succeeds_if_artifact_completed():
    client = MagicMock()
    # poll_status keeps returning in_progress (e.g. media_ready lag)
    client.artifacts.poll_status = AsyncMock(
        return_value=SimpleNamespace(
            status="in_progress",
            is_complete=False,
            is_failed=False,
            is_removed=False,
        )
    )
    # Direct list check finds the artifact completed (status=3)
    client.artifacts.list = AsyncMock(
        return_value=[
            SimpleNamespace(
                id="artifact-123",
                title="Completed In Fallback",
                status=3,
                created_at=None,
            )
        ]
    )

    # Start time way in the past so t > MAX_POLL_TIMEOUT_SECONDS immediately
    result = await _poll_single_task(
        client,
        "notebook",
        "artifact-123",
        "en",
        generation_started_at=0.0,
    )

    assert result["status"] == TaskStatus.COMPLETED
    assert result["title"] == "Completed In Fallback"
    assert result["artifact_id"] == "artifact-123"


@pytest.mark.anyio
async def test_poll_single_task_times_out_when_fallback_not_completed():
    client = MagicMock()
    client.artifacts.poll_status = AsyncMock(
        return_value=SimpleNamespace(
            status="in_progress",
            is_complete=False,
            is_failed=False,
            is_removed=False,
        )
    )
    # Direct list check finds artifact still in progress (status=2)
    client.artifacts.list = AsyncMock(
        return_value=[
            SimpleNamespace(
                id="artifact-456",
                title="Still Running",
                status=2,
                created_at=None,
            )
        ]
    )

    result = await _poll_single_task(
        client,
        "notebook",
        "artifact-456",
        "en",
        generation_started_at=0.0,
    )

    assert result["status"] == TaskStatus.FAILED
    assert "timed out" in result["error"]


def test_extract_json_payload_clean_and_fenced():
    from podcaster.audio_gen.prompting import extract_json_payload

    assert extract_json_payload('{"key": "value"}') == {"key": "value"}
    assert extract_json_payload('```json\n{"key": "value"}\n```') == {"key": "value"}
    assert extract_json_payload('```\n{"key": "value"}\n```') == {"key": "value"}


def test_resolve_two_roles_list_and_fallback():
    from podcaster.audio_gen.prompting import resolve_two_roles

    h, g = resolve_two_roles({"roles": ["Host A", "Guest B"]})
    assert h == "Host A"
    assert g == "Guest B"

    h2, g2 = resolve_two_roles(
        {"host_role": "Custom Host", "guest_role": "Custom Guest"}
    )
    assert h2 == "Custom Host"
    assert g2 == "Custom Guest"

    h3, g3 = resolve_two_roles({})
    assert h3 == "Host"
    assert g3 == "Co-host"


def test_render_prompt_template(tmp_path):
    from podcaster.audio_gen.prompting import render_prompt_template

    tmpl = tmp_path / "test.j2"
    tmpl.write_text("Hello ${ name }! Length: ${ length }", encoding="utf-8")

    rendered = render_prompt_template(tmpl, name="World", length="15 minutes")
    assert rendered == "Hello World! Length: 15 minutes"
