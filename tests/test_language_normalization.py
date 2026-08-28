"""Unit tests for command line language string normalization."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from podcaster.audio_gen.core import create_podcast_audio_jobs
from podcaster.cli import cli
from podcaster.config import NotebookLMConfig, PodcastGenerationConfig
from podcaster.models import TaskStatus


class DummyInputs(BaseModel):
    foo: str = "bar"


@pytest.mark.anyio
async def test_create_podcast_audio_jobs_normalizes_language_to_lowercase():
    mock_status = AsyncMock()
    mock_status.status = TaskStatus.IN_PROGRESS
    mock_status.task_id = "test-task-123"

    mock_client = AsyncMock()
    mock_client.artifacts.generate_audio.return_value = mock_status

    with (
        patch("podcaster.audio_gen.core.get_notebooklm_client") as mock_get_client,
        patch("podcaster.audio_gen.core.load_plugin") as mock_load_plugin,
    ):
        mock_get_client.return_value.__aenter__.return_value = mock_client

        mock_plugin = MagicMock()
        mock_plugin.Inputs = DummyInputs
        mock_plugin.get_prompt = AsyncMock(return_value="prompt")
        mock_load_plugin.return_value = mock_plugin

        tasks = []
        async for t in create_podcast_audio_jobs(
            notebook_id="nb-123",
            type_name="main-article-with-author",
            languages=["EN", "Fr-FR", "De"],
            length_str="short",
            format_args={},
            generator_config=PodcastGenerationConfig(),
            notebooklm_config=NotebookLMConfig(),
        ):
            tasks.append(t)

        assert len(tasks) == 3
        langs = [t.metadata["generate-podcast"]["language"] for t in tasks]
        assert langs == ["en", "fr-fr", "de"]


@pytest.mark.anyio
async def test_create_podcast_audio_jobs_submits_languages_sequentially():
    english_request_started = asyncio.Event()
    release_english_request = asyncio.Event()
    submitted_languages = []
    mock_client = AsyncMock()

    async def generate_audio(notebook_id, language, instructions, audio_length):
        del notebook_id, instructions, audio_length
        submitted_languages.append(language)
        if language == "en":
            english_request_started.set()
            await release_english_request.wait()
        return MagicMock(status=TaskStatus.IN_PROGRESS, task_id=f"task-{language}")

    mock_client.artifacts.generate_audio.side_effect = generate_audio

    with (
        patch("podcaster.audio_gen.core.get_notebooklm_client") as mock_get_client,
        patch("podcaster.audio_gen.core.load_plugin") as mock_load_plugin,
    ):
        mock_get_client.return_value.__aenter__.return_value = mock_client
        mock_plugin = MagicMock()
        mock_plugin.Inputs = DummyInputs
        mock_plugin.get_prompt = AsyncMock(return_value="prompt")
        mock_load_plugin.return_value = mock_plugin

        collect_task = asyncio.create_task(
            _collect_audio_jobs(
                languages=["en", "fr"], generator_config=PodcastGenerationConfig()
            )
        )
        await english_request_started.wait()
        assert submitted_languages == ["en"]
        release_english_request.set()
        tasks = await collect_task

    assert [task.task_id for task in tasks] == ["task-en", "task-fr"]


async def _collect_audio_jobs(languages, generator_config):
    return [
        task
        async for task in create_podcast_audio_jobs(
            notebook_id="nb-123",
            type_name="main-article-with-author",
            languages=languages,
            length_str="short",
            format_args={},
            generator_config=generator_config,
            notebooklm_config=NotebookLMConfig(),
        )
    ]


def test_podcast_create_cli_normalizes_languages():
    runner = CliRunner()
    with patch(
        "podcaster.audio_gen.core.create_podcast_audio_jobs"
    ) as mock_create_jobs:

        async def dummy_gen(*args, **kwargs):
            if False:
                yield None

        mock_create_jobs.return_value = dummy_gen()

        result = runner.invoke(
            cli,
            ["podcast", "create", "nb-123", "type-1", "-l", "EN", "-l", "FR-FR"],
        )

        assert result.exit_code == 0
        mock_create_jobs.assert_called_once()
        call_args = mock_create_jobs.call_args
        assert call_args[0][2] == ["en", "fr-fr"]


@pytest.mark.anyio
async def test_workflow_run_normalizes_languages(tmp_path, dbos_session):
    with (
        patch(
            "podcaster.notebook.init_notebook", new_callable=AsyncMock
        ) as mock_init_nb,
        patch("podcaster.audio_gen.core.create_podcast_audio_jobs") as mock_create_jobs,
        patch("podcaster.research.create_research_job") as mock_research,
        patch("podcaster.cover.create_cover_job") as mock_cover,
    ):
        mock_init_nb.return_value = {
            "notebook_id": "nb-123",
            "derived_title": "Test Title",
            "source_id": "src-123",
        }

        async def dummy_gen(*args, **kwargs):
            if False:
                yield None

        mock_create_jobs.return_value = dummy_gen()
        mock_research.return_value = AsyncMock()
        mock_cover.return_value = AsyncMock()

        from podcaster.config import (
            EnrichWebConfig,
            GenerateCoverConfig,
            ImporterConfig,
            NativeImporterConfig,
            PodcastTagsConfig,
            PodcastTranscriptionConfig,
            TaggingConfig,
            TranscribeConfig,
        )
        from podcaster.workflows.deep_dive_article.config import DeepDiveArticleConfig
        from podcaster.workflows.deep_dive_article.workflow import (
            deep_dive_article_workflow,
        )

        wf_config = DeepDiveArticleConfig(
            type="deep_dive_article",
            podcast_generator=PodcastGenerationConfig(languages=["EN"]),
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

        await deep_dive_article_workflow(
            preset_name="default",
            wf_config=wf_config,
            workdir=str(tmp_path),
            workflow_id="wf_test_norm",
            title="Test Title",
            source_file="test.pdf",
            notebook_id=None,
            length="default",
            languages=["EN", "Es-ES"],
            enrich_web=False,
            generate_cover=False,
            transcribe=False,
            gcp_config=None,
            notebooklm_config=NotebookLMConfig(),
        )

        # Check that create_podcast_audio_jobs received lowercased languages
        assert mock_create_jobs.call_count == 1
        call_args = mock_create_jobs.call_args
        assert call_args[0][2] == ["en", "es-es"]
