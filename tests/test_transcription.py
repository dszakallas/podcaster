"""Tests for transcription job submission."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from podcaster.config import GCPConfig, PodcastTranscriptionConfig
from podcaster.models import PodcastGenArtifact
from podcaster.transcription import create_transcription_jobs, generate_lrc, to_bcp47


def test_generate_lrc_output(tmp_path):
    response = SimpleNamespace(
        results={
            "gs://bucket/file.wav": SimpleNamespace(
                error=SimpleNamespace(code=0, message=""),
                transcript=SimpleNamespace(
                    results=[
                        SimpleNamespace(
                            alternatives=[
                                SimpleNamespace(
                                    transcript="Hello world, this is a podcast.",
                                    words=[
                                        SimpleNamespace(
                                            word="Hello",
                                            start_offset=timedelta(seconds=0),
                                        ),
                                        SimpleNamespace(
                                            word="world,",
                                            start_offset=timedelta(seconds=0.5),
                                        ),
                                        SimpleNamespace(
                                            word="this",
                                            start_offset=timedelta(seconds=2.1),
                                        ),
                                        SimpleNamespace(
                                            word="is",
                                            start_offset=timedelta(seconds=2.5),
                                        ),
                                        SimpleNamespace(
                                            word="a",
                                            start_offset=timedelta(seconds=3.0),
                                        ),
                                        SimpleNamespace(
                                            word="podcast.",
                                            start_offset=timedelta(seconds=4.2),
                                        ),
                                    ],
                                )
                            ]
                        )
                    ]
                ),
            )
        }
    )
    lrc_file = tmp_path / "test.lrc"
    generate_lrc(response, str(lrc_file), speed_factor=1.0)  # type: ignore[arg-type]
    content = lrc_file.read_text()
    assert "[00:00.00]Hello world," in content
    assert "[00:02.10]this is a" in content
    assert "[00:04.20]podcast." in content


def test_to_bcp47_mappings():
    assert to_bcp47("en") == "en-US"
    assert to_bcp47("cs") == "cs-CZ"
    assert to_bcp47("de") == "de-DE"
    assert to_bcp47("ar") == "ar-SA"
    assert to_bcp47("pt") == "pt-PT"
    assert to_bcp47("sw") == "sw-KE"
    assert to_bcp47("en-US") == "en-US"
    assert to_bcp47("invalid-unknown") == "invalid-unknown"


@pytest.mark.anyio
async def test_create_transcription_jobs_propagates_submission_failure(tmp_path):
    audio_path = tmp_path / "episode.m4a"
    audio_path.write_bytes(b"audio")
    artifact = PodcastGenArtifact(
        notebook_id="notebook",
        artifact_id="artifact",
        title="Episode",
        path=str(audio_path),
        filename=audio_path.name,
        metadata={"generate-podcast": {"language": "cs"}},
    )
    submission_error = ConnectionError("DNS query cancelled")
    speech_client = MagicMock()
    speech_client.batch_recognize.side_effect = submission_error

    async def artifacts():
        yield artifact

    with (
        patch("podcaster.transcription.SpeechClient", return_value=speech_client),
        patch("podcaster.transcription.preprocess_audio"),
        patch(
            "podcaster.transcription.upload_to_gcs",
            new_callable=AsyncMock,
            return_value="gs://bucket/transcriptions/episode.wav",
        ),
        patch("podcaster.transcription.delete_from_gcs", new_callable=AsyncMock),
        pytest.raises(ConnectionError, match="DNS query cancelled"),
    ):
        async for _ in create_transcription_jobs(
            artifacts(),
            GCPConfig(project_id="project", gcs_bucket="bucket"),
            PodcastTranscriptionConfig(),
        ):
            pass
