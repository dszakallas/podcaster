"""Unit tests for workflow step functions and gcp_config handling."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from podcaster.config import (
    EnrichWebConfig,
    GCPConfig,
    GenerateCoverConfig,
    GenerateCoverSpecConfig,
    ImporterConfig,
    NativeImporterConfig,
    NotebookLMConfig,
    PodcastGenerationConfig,
    PodcastTagsConfig,
    PodcastTranscriptionConfig,
    TaggingConfig,
    TranscribeConfig,
)
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
    TaskStatus,
    TranscriptionTask,
)
from podcaster.workflows.deep_dive_article.config import DeepDiveArticleConfig
from podcaster.workflows.deep_dive_article.workflow import (
    deep_dive_article_workflow,
    generate_cover_step,
    process_single_audio_task_step,
    tag_audio_artifact_step,
    transcribe_audio_artifact_step,
)


def test_process_single_audio_task_step_gcp_config_passed(dbos_session):
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

            await process_single_audio_task_step(
                notebook_id="test-nb",
                notebook_title="Test notebook",
                notebook_created_at="2026-08-24T12:00:00Z",
                task_info=task_info,
                cover_image_path=None,
                working_dir="podcasts/wf_test123",
                transcribe=True,
                transcription_languages=["en"],
                transcribe_retry_count=1,
                transcription_config=PodcastTranscriptionConfig(),
                tagging_config=None,
                gcp_config=custom_gcp,
                notebooklm_config=NotebookLMConfig(),
            )

            mock_create_jobs.assert_called_once()
            assert mock_create_jobs.call_args.kwargs["gcp_config"] == custom_gcp

    asyncio.run(_test())


def test_tag_audio_artifact_step_uses_notebook_metadata(dbos_session):
    async def _test():
        artifact = PodcastGenArtifact(
            notebook_id="test-nb",
            artifact_id="artifact-1",
            title="Episode title",
            path="episode.m4a",
            filename="episode.m4a",
        )
        captured: dict[str, object] = {}

        async def mock_tag(artifacts, **kwargs):
            captured.update(kwargs)
            async for item in artifacts:
                yield item

        with patch(
            "podcaster.workflows.deep_dive_article.workflow.tagging.tag_artifacts",
            side_effect=mock_tag,
        ):
            await tag_audio_artifact_step(
                artifact,
                cover_image_path=None,
                album="Notebook title",
                created_at="2026-08-24T12:00:00Z",
                tags_config=PodcastTagsConfig(),
            )

        assert captured["album"] == "Notebook title"
        assert captured["created_at"] == "2026-08-24T12:00:00Z"

    asyncio.run(_test())


def test_process_single_audio_task_step_fails_after_transcription_retries(
    dbos_session,
):
    async def _test():
        task_info = PodcastGenTask(
            notebook_id="test-nb",
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            metadata={"generate-podcast": {"language": "en"}},
        )

        async def mock_poll(tasks, **kwargs):
            async for task in tasks:
                yield task

        async def mock_download(tasks, **kwargs):
            async for _ in tasks:
                yield PodcastGenArtifact(
                    notebook_id="test-nb",
                    artifact_id="task-1",
                    title="Test",
                    path="test.m4a",
                    filename="test.m4a",
                )

        attempts = 0

        async def failing_create_jobs(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise ConnectionError("transcription service unavailable")
            yield

        with (
            patch(
                "podcaster.workflows.deep_dive_article.workflow.audio_gen_core.poll_tasks",
                side_effect=mock_poll,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.audio_gen_core.download_artifacts",
                side_effect=mock_download,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.transcription.create_transcription_jobs",
                side_effect=failing_create_jobs,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            with pytest.raises(
                RuntimeError,
                match="Transcription failed after 2 attempts: transcription service unavailable",
            ):
                await process_single_audio_task_step(
                    notebook_id="test-nb",
                    notebook_title="Test notebook",
                    notebook_created_at="2026-08-24T12:00:00Z",
                    task_info=task_info,
                    cover_image_path=None,
                    working_dir="podcasts/wf_test123",
                    transcribe=True,
                    transcription_languages=["en"],
                    transcribe_retry_count=1,
                    transcription_config=PodcastTranscriptionConfig(),
                    tagging_config=None,
                    gcp_config=GCPConfig(),
                    notebooklm_config=NotebookLMConfig(),
                )

        assert attempts == 2

    asyncio.run(_test())


def test_transcription_step_does_not_retry_permanent_failure(dbos_session):
    async def _test():
        artifact = PodcastGenArtifact(
            notebook_id="test-nb",
            artifact_id="task-1",
            title="Test",
            path="test.m4a",
            filename="test.m4a",
        )
        attempts = 0

        async def failing_create_jobs(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise ValueError("invalid transcription request")
            yield

        with patch(
            "podcaster.workflows.deep_dive_article.workflow.transcription.create_transcription_jobs",
            side_effect=failing_create_jobs,
        ):
            with pytest.raises(ValueError, match="invalid transcription request"):
                await transcribe_audio_artifact_step(
                    artifact,
                    retry_count=2,
                    transcription_config=PodcastTranscriptionConfig(),
                    gcp_config=GCPConfig(),
                )

        assert attempts == 1

    asyncio.run(_test())


def test_generate_cover_step_reuses_created_job_when_retrying(dbos_session):
    async def _test():
        calls = []

        async def fake_generate_cover(
            notebook_id,
            working_dir,
            notebooklm_client,
            model,
            task_id=None,
            image_gen_prompt=None,
            on_start_callback=None,
        ):
            calls.append((task_id, image_gen_prompt))
            if task_id is None:
                assert on_start_callback is not None
                await on_start_callback("cover-job", "a generated prompt")
                raise RuntimeError("poll failed")
            return "podcasts/cover.jpg"

        @asynccontextmanager
        async def fake_notebooklm_client(_config):
            yield object()

        with (
            patch(
                "podcaster.workflows.deep_dive_article.workflow.cover.generate_cover_for_notebook",
                side_effect=fake_generate_cover,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.get_notebooklm_client",
                side_effect=fake_notebooklm_client,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            result = await generate_cover_step(
                "notebook-id",
                "podcasts",
                NotebookLMConfig(),
                cover_spec=GenerateCoverSpecConfig(),
                retry_count=1,
            )

        assert result == "podcasts/cover.jpg"
        assert calls == [(None, None), ("cover-job", "a generated prompt")]

    asyncio.run(_test())


def test_generate_cover_step_passes_configured_model(dbos_session):
    async def _test():
        captured_model = []

        async def fake_generate_cover(
            notebook_id,
            working_dir,
            notebooklm_client,
            model,
            task_id=None,
            image_gen_prompt=None,
            on_start_callback=None,
        ):
            captured_model.append(model)
            return "podcasts/cover.jpg"

        @asynccontextmanager
        async def fake_notebooklm_client(_config):
            yield object()

        with (
            patch(
                "podcaster.workflows.deep_dive_article.workflow.cover.generate_cover_for_notebook",
                side_effect=fake_generate_cover,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.get_notebooklm_client",
                side_effect=fake_notebooklm_client,
            ),
        ):
            result = await generate_cover_step(
                "notebook-id",
                "podcasts",
                NotebookLMConfig(),
                cover_spec=GenerateCoverSpecConfig(model="custom-gemini-cover-model"),
                retry_count=1,
            )

        assert result == "podcasts/cover.jpg"
        assert captured_model == ["custom-gemini-cover-model"]

    asyncio.run(_test())


def test_deep_dive_workflow_runs_cover_and_enrichment_concurrently(
    dbos_session, tmp_path
):
    async def _test():
        cover_started = asyncio.Event()
        enrichment_started = asyncio.Event()

        async def init_notebook(*args, **kwargs):
            return {
                "notebook_id": "nb-1",
                "derived_title": "Test",
                "source_id": "src-1",
            }

        async def generate_cover(*args, **kwargs):
            cover_started.set()
            await enrichment_started.wait()
            return "cover.png"

        async def enrich_source(*args, **kwargs):
            await cover_started.wait()
            enrichment_started.set()
            return object()

        async def create_audio_jobs(*args, **kwargs):
            return []

        workflow_config = DeepDiveArticleConfig(
            type="deep_dive_article",
            podcast_generator=PodcastGenerationConfig(),
            importer=ImporterConfig(native=NativeImporterConfig()),
            enrich_web=EnrichWebConfig(enable=True),
            generate_cover=GenerateCoverConfig(enable=True),
            transcribe=TranscribeConfig(
                enable=False,
                podcast_transcriber=PodcastTranscriptionConfig(),
            ),
            tagging=TaggingConfig(enable=False, spec=PodcastTagsConfig()),
            distribute=[],
        )

        with (
            patch(
                "podcaster.workflows.deep_dive_article.workflow.init_notebook_step",
                side_effect=init_notebook,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.generate_cover_step",
                side_effect=generate_cover,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.enrich_source_step",
                side_effect=enrich_source,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.create_podcast_audio_jobs_step",
                side_effect=create_audio_jobs,
            ),
        ):
            await asyncio.wait_for(
                deep_dive_article_workflow(
                    preset_name="test",
                    wf_config=workflow_config,
                    workdir=str(tmp_path),
                    workflow_id="wf-parallelism",
                    source_url="source.txt",
                    notebooklm_config=NotebookLMConfig(),
                ),
                timeout=0.2,
            )

    asyncio.run(_test())


def test_deep_dive_workflow_processes_audio_tasks_concurrently(
    dbos_session, tmp_path
):
    async def _test():
        both_tasks_started = asyncio.Event()

        async def init_notebook(*args, **kwargs):
            return {
                "notebook_id": "nb-1",
                "derived_title": "Test",
                "source_id": "src-1",
            }

        async def create_audio_jobs(*args, **kwargs):
            return [
                PodcastGenTask(notebook_id="nb-1", task_id="task-en"),
                PodcastGenTask(notebook_id="nb-1", task_id="task-fr"),
            ]

        async def process_audio_task(*args, task_info, **kwargs):
            if task_info.task_id == "task-fr":
                both_tasks_started.set()
            await both_tasks_started.wait()
            return PodcastGenArtifact(
                notebook_id="nb-1",
                artifact_id=task_info.task_id,
                title="Test",
                path=f"{task_info.task_id}.m4a",
                filename=f"{task_info.task_id}.m4a",
            )

        workflow_config = DeepDiveArticleConfig(
            type="deep_dive_article",
            podcast_generator=PodcastGenerationConfig(),
            importer=ImporterConfig(native=NativeImporterConfig()),
            enrich_web=EnrichWebConfig(enable=False),
            generate_cover=GenerateCoverConfig(enable=False),
            transcribe=TranscribeConfig(
                enable=False,
                podcast_transcriber=PodcastTranscriptionConfig(),
            ),
            tagging=TaggingConfig(enable=False, spec=PodcastTagsConfig()),
            distribute=[],
        )

        with (
            patch(
                "podcaster.workflows.deep_dive_article.workflow.init_notebook_step",
                side_effect=init_notebook,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.create_podcast_audio_jobs_step",
                side_effect=create_audio_jobs,
            ),
            patch(
                "podcaster.workflows.deep_dive_article.workflow.process_single_audio_task_step",
                side_effect=process_audio_task,
            ),
        ):
            result = await asyncio.wait_for(
                deep_dive_article_workflow(
                    preset_name="test",
                    wf_config=workflow_config,
                    workdir=str(tmp_path),
                    workflow_id="wf-audio-parallelism",
                    source_url="source.txt",
                    notebooklm_config=NotebookLMConfig(),
                ),
                timeout=0.2,
            )

        assert result["files"] == ["task-en.m4a", "task-fr.m4a"]

    asyncio.run(_test())
