"""Unit tests for workflow helper functions and gcp_config handling."""

import asyncio
from unittest.mock import patch

from podcaster.config import GCPConfig, PodcastTranscriptionConfig
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
    TaskStatus,
    TranscriptionTask,
)
from podcaster.workflows.deep_dive_article.workflow import (
    generate_download_and_tag_podcast,
)


def test_generate_download_and_tag_podcast_gcp_config_passed():
    async def _test():
        custom_gcp = GCPConfig(
            project_id="test-proj", gcs_bucket="test-bucket", location="us-central1"
        )
        task_info = PodcastGenTask(
            notebook_id="test-nb",
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            metadata={"generate-podcast": {"language": "en"}},
        )

        async def mock_poll(tasks, **kwargs):
            async for t in tasks:
                yield task_info

        async def mock_dl(tasks, **kwargs):
            async for t in tasks:
                yield PodcastGenArtifact(
                    notebook_id="test-nb",
                    artifact_id="task-1",
                    title="Test",
                    path="test.m4a",
                    filename="test.m4a",
                )

        trans_task = TranscriptionTask(
            artifact_id="task-1",
            path="test.m4a",
            task_id="tr-1",
            gcs_uri="gs://bucket/audio.m4a",
            lrc_path="test.lrc",
            transcript_path="test.tr.json",
        )

        async def mock_create_jobs_gen(*args, **kwargs):
            yield trans_task

        async def mock_poll_jobs_gen(*args, **kwargs):
            yield trans_task

        async def mock_dl_jobs_gen(*args, **kwargs):
            yield trans_task

        with (
            patch(
                "podcaster.workflows.deep_dive_article.workflow.transcription.create_transcription_jobs",
                side_effect=mock_create_jobs_gen,
            ) as mock_create_jobs,
            patch(
                "podcaster.workflows.deep_dive_article.workflow.transcription.poll_transcription_jobs",
                side_effect=mock_poll_jobs_gen,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.transcription.download_transcription_jobs",
                side_effect=mock_dl_jobs_gen,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.os.path.exists",
                return_value=True,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.audio_gen_core.poll_tasks",
                side_effect=mock_poll,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.audio_gen_core.download_artifacts",
                side_effect=mock_dl,
            ),
        ):

            await generate_download_and_tag_podcast(
                notebook_id="test-nb",
                task_info=task_info,
                cover_image=None,
                podcast_dir="podcasts",
                transcribe=True,
                transcription_languages=["en"],
                gcp_config=custom_gcp,
                transcription_config=PodcastTranscriptionConfig(),
            )

            mock_create_jobs.assert_called_once()
            assert mock_create_jobs.call_args.kwargs["gcp_config"] == custom_gcp

    asyncio.run(_test())
