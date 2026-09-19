"""Unit tests for workflow step functions and gcp_config handling."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from podcaster import tagging
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
from podcaster.workflows.common import (
    AudioProcessingOptions,
    WorkflowEnvironment,
    WorkflowNotebookContext,
    generate_cover_step,
    poll_audio_tasks_step,
    process_audio_tasks,
    process_single_audio_task_step,
    tag_audio_artifact_step,
    transcribe_audio_artifact_step,
)
from podcaster.workflows.deep_dive_article.config import DeepDiveArticleConfig
from podcaster.workflows.deep_dive_article.workflow import (
    DeepDiveArticleOverrides,
    deep_dive_article_workflow,
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
            async for _ in tasks:
                yield task_info

        async def mock_dl(tasks, **kwargs):
            async for _ in tasks:
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
                "podcaster.workflows.common.transcription.create_transcription_jobs",
                side_effect=mock_create_jobs_gen,
            ) as mock_create_jobs,
            patch(
                "podcaster.workflows.common.transcription.poll_transcription_jobs",
                side_effect=mock_poll_jobs_gen,
            ),
            patch(
                "podcaster.workflows.common.transcription.download_transcription_jobs",
                side_effect=mock_dl_jobs_gen,
            ),
            patch(
                "podcaster.workflows.common.os.path.exists",
                return_value=True,
            ),
            patch(
                "podcaster.workflows.common.audio_gen_core.poll_tasks",
                side_effect=mock_poll,
            ),
            patch(
                "podcaster.workflows.common.audio_gen_core.download_artifacts",
                side_effect=mock_dl,
            ),
        ):

            context = WorkflowNotebookContext(
                notebook_id="test-nb",
                title="Test notebook",
                created_at="2026-08-24T12:00:00Z",
                working_dir="podcasts/wf_test123",
            )
            options = AudioProcessingOptions(
                notebooklm_config=NotebookLMConfig(),
                transcribe=True,
                transcription_languages=["en"],
                transcribe_retry_count=1,
                transcription_config=PodcastTranscriptionConfig(),
                gcp_config=custom_gcp,
            )
            await process_single_audio_task_step(
                context=context,
                task_info=task_info,
                options=options,
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
            "podcaster.workflows.common.tagging.tag_artifacts",
            side_effect=mock_tag,
        ):
            await tag_audio_artifact_step(
                artifact,
                cover_image_path=None,
                album="Notebook title",
                created_at="2026-08-24T12:00:00Z",
                tags_config=PodcastTagsConfig(),
                track=2,
                total_tracks=3,
            )

        assert captured["album"] == "Notebook title"
        assert captured["created_at"] == "2026-08-24T12:00:00Z"
        assert captured["track_offset"] == 1
        assert captured["total_tracks"] == 3

    asyncio.run(_test())


def test_tag_artifacts_uses_explicit_metadata_track():
    async def _test():
        artifact1 = PodcastGenArtifact(
            notebook_id="test-nb",
            artifact_id="art-1",
            title="Episode 1 en",
            path="ep1_en.m4a",
            filename="ep1_en.m4a",
            metadata={"track": 1, "total_tracks": 2},
        )
        artifact2 = PodcastGenArtifact(
            notebook_id="test-nb",
            artifact_id="art-2",
            title="Episode 1 cs",
            path="ep1_cs.m4a",
            filename="ep1_cs.m4a",
            metadata={"track": 1, "total_tracks": 2},
        )
        artifact3 = PodcastGenArtifact(
            notebook_id="test-nb",
            artifact_id="art-3",
            title="Episode 2 en",
            path="ep2_en.m4a",
            filename="ep2_en.m4a",
            metadata={"track": 2, "total_tracks": 2},
        )

        tagged_tracks = []
        with patch("podcaster.tagging.tag_file") as mock_tag_file:

            async def art_stream():
                for a in [artifact1, artifact2, artifact3]:
                    yield a

            async for tagged in tagging.tag_artifacts(
                art_stream(),
                tags_config=PodcastTagsConfig(),
            ):
                tag_meta = tagged.metadata.get("tag-podcast")
                track_val = tag_meta.get("track") if tag_meta else None
                assert track_val is not None
                tagged_tracks.append(track_val)

        assert tagged_tracks == [1, 1, 2]
        assert mock_tag_file.call_count == 3
        assert mock_tag_file.call_args_list[0].kwargs["tags"].track == 1
        assert mock_tag_file.call_args_list[0].kwargs["tags"].total_tracks == 2
        assert mock_tag_file.call_args_list[1].kwargs["tags"].track == 1
        assert mock_tag_file.call_args_list[1].kwargs["tags"].total_tracks == 2
        assert mock_tag_file.call_args_list[2].kwargs["tags"].track == 2
        assert mock_tag_file.call_args_list[2].kwargs["tags"].total_tracks == 2

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
                "podcaster.workflows.common.audio_gen_core.poll_tasks",
                side_effect=mock_poll,
            ),
            patch(
                "podcaster.workflows.common.audio_gen_core.download_artifacts",
                side_effect=mock_download,
            ),
            patch(
                "podcaster.workflows.common.transcription.create_transcription_jobs",
                side_effect=failing_create_jobs,
            ),
            patch(
                "podcaster.workflows.common.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            pytest.raises(
                RuntimeError,
                match="Transcription failed after 2 attempts: transcription service unavailable",
            ),
        ):
            context = WorkflowNotebookContext(
                notebook_id="test-nb",
                title="Test notebook",
                created_at="2026-08-24T12:00:00Z",
                working_dir="podcasts/wf_test123",
            )
            options = AudioProcessingOptions(
                notebooklm_config=NotebookLMConfig(),
                transcribe=True,
                transcription_languages=["en"],
                transcribe_retry_count=1,
                transcription_config=PodcastTranscriptionConfig(),
                gcp_config=GCPConfig(),
            )
            await process_single_audio_task_step(
                context=context,
                task_info=task_info,
                options=options,
            )

        assert attempts == 2

    asyncio.run(_test())


def test_poll_audio_tasks_step_completes_in_order(dbos_session):
    async def _test():
        task1 = PodcastGenTask(notebook_id="test-nb", task_id="task-1")
        task2 = PodcastGenTask(notebook_id="test-nb", task_id="task-2")

        async def mock_poll(tasks, **kwargs):
            # Yield out of order
            yield PodcastGenTask(
                notebook_id="test-nb",
                task_id="task-2",
                status=TaskStatus.COMPLETED,
            )
            yield PodcastGenTask(
                notebook_id="test-nb",
                task_id="task-1",
                status=TaskStatus.COMPLETED,
            )

        with patch(
            "podcaster.workflows.common.audio_gen_core.poll_tasks",
            side_effect=mock_poll,
        ):
            results = await poll_audio_tasks_step(
                tasks=[task1, task2],
                notebooklm_config=NotebookLMConfig(),
            )
            assert len(results) == 2
            assert results[0].task_id == "task-1"
            assert results[1].task_id == "task-2"

    asyncio.run(_test())


def test_poll_audio_tasks_step_raises_on_failure(dbos_session):
    async def _test():
        task1 = PodcastGenTask(notebook_id="test-nb", task_id="task-1")

        async def mock_poll(tasks, **kwargs):
            yield PodcastGenTask(
                notebook_id="test-nb",
                task_id="task-1",
                status=TaskStatus.FAILED,
                error="Generation failed: quota exceeded",
            )

        with (
            patch(
                "podcaster.workflows.common.audio_gen_core.poll_tasks",
                side_effect=mock_poll,
            ),
            pytest.raises(RuntimeError, match="quota exceeded"),
        ):
            await poll_audio_tasks_step(
                tasks=[task1],
                notebooklm_config=NotebookLMConfig(),
            )

    asyncio.run(_test())


def test_process_audio_tasks_processes_all(dbos_session):
    async def _test():
        task1 = PodcastGenTask(
            notebook_id="test-nb",
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            metadata={"generate-podcast": {"language": "en"}},
        )
        task2 = PodcastGenTask(
            notebook_id="test-nb",
            task_id="task-2",
            status=TaskStatus.COMPLETED,
            metadata={"generate-podcast": {"language": "cs"}},
        )

        async def mock_poll(tasks, **kwargs):
            yield task1
            yield task2

        async def mock_dl(tasks, **kwargs):
            async for t in tasks:
                yield PodcastGenArtifact(
                    notebook_id="test-nb",
                    artifact_id=t.task_id,
                    title=f"Title {t.task_id}",
                    path=f"{t.task_id}.m4a",
                    filename=f"{t.task_id}.m4a",
                )

        with (
            patch(
                "podcaster.workflows.common.audio_gen_core.poll_tasks",
                side_effect=mock_poll,
            ),
            patch(
                "podcaster.workflows.common.audio_gen_core.download_artifacts",
                side_effect=mock_dl,
            ),
        ):
            context = WorkflowNotebookContext(
                notebook_id="test-nb",
                title="Test Title",
                working_dir="podcasts/test_wf",
            )
            options = AudioProcessingOptions(
                notebooklm_config=NotebookLMConfig(),
            )
            artifacts = await process_audio_tasks(
                context=context,
                audio_tasks=[task1, task2],
                options=options,
            )
            assert len(artifacts) == 2
            assert artifacts[0].artifact_id == "task-1"
            assert artifacts[1].artifact_id == "task-2"

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

        with (
            patch(
                "podcaster.workflows.common.transcription.create_transcription_jobs",
                side_effect=failing_create_jobs,
            ),
            pytest.raises(ValueError, match="invalid transcription request"),
        ):
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
                "podcaster.workflows.common.cover.generate_cover_for_notebook",
                side_effect=fake_generate_cover,
            ),
            patch(
                "podcaster.workflows.common.get_notebooklm_client",
                side_effect=fake_notebooklm_client,
            ),
            patch(
                "podcaster.workflows.common.asyncio.sleep",
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
                "podcaster.workflows.common.cover.generate_cover_for_notebook",
                side_effect=fake_generate_cover,
            ),
            patch(
                "podcaster.workflows.common.get_notebooklm_client",
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
            env = WorkflowEnvironment(
                workdir=str(tmp_path),
                workflow_id="wf-parallelism",
                notebooklm_config=NotebookLMConfig(),
            )
            overrides = DeepDiveArticleOverrides(source_url="source.txt")
            await asyncio.wait_for(
                deep_dive_article_workflow(
                    preset_name="test",
                    wf_config=workflow_config,
                    env=env,
                    overrides=overrides,
                ),
                timeout=0.2,
            )

    asyncio.run(_test())


def test_deep_dive_workflow_processes_audio_tasks_concurrently(dbos_session, tmp_path):
    async def _test():
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

        async def process_audio_tasks_mock(*args, audio_tasks, **kwargs):
            return [
                PodcastGenArtifact(
                    notebook_id="nb-1",
                    artifact_id=task_info.task_id,
                    title="Test",
                    path=f"{task_info.task_id}.m4a",
                    filename=f"{task_info.task_id}.m4a",
                )
                for task_info in audio_tasks
            ]

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
                "podcaster.workflows.deep_dive_article.workflow.process_audio_tasks",
                side_effect=process_audio_tasks_mock,
            ),
        ):
            env = WorkflowEnvironment(
                workdir=str(tmp_path),
                workflow_id="wf-audio-parallelism",
                notebooklm_config=NotebookLMConfig(),
            )
            overrides = DeepDiveArticleOverrides(source_url="source.txt")
            result = await asyncio.wait_for(
                deep_dive_article_workflow(
                    preset_name="test",
                    wf_config=workflow_config,
                    env=env,
                    overrides=overrides,
                ),
                timeout=0.2,
            )

        assert result["files"] == ["task-en.m4a", "task-fr.m4a"]

    asyncio.run(_test())
