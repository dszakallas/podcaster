"""Tests for transcription job submission."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from podcaster.config import GCPConfig, PodcastTranscriptionConfig
from podcaster.models import PodcastGenArtifact
from podcaster.transcription import create_transcription_jobs


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
    ):
        with pytest.raises(ConnectionError, match="DNS query cancelled"):
            async for _ in create_transcription_jobs(
                artifacts(),
                GCPConfig(project_id="project", gcs_bucket="bucket"),
                PodcastTranscriptionConfig(),
            ):
                pass
