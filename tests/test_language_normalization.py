"""Unit tests for command line language string normalization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from podcaster.audio_gen.core import create_podcast_audio_jobs
from podcaster.cli import cli
from podcaster.config import PodcastGenerationConfig
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
        ):
            tasks.append(t)

        assert len(tasks) == 3
        langs = [t.metadata["generate-podcast"]["language"] for t in tasks]
        assert langs == ["en", "fr-fr", "de"]


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
async def test_workflow_run_normalizes_languages(tmp_path):
    with (
        patch("podcaster.notebook.init_notebook") as mock_init_nb,
        patch("podcaster.audio_gen.core.create_podcast_audio_jobs") as mock_create_jobs,
        patch("podcaster.research.create_research_job") as mock_research,
        patch("podcaster.cover.create_cover_job") as mock_cover,
    ):
        (tmp_path / "2026-08-16-test-title").mkdir(parents=True, exist_ok=True)
        mock_init_nb.return_value = {
            "notebook_id": "nb-123",
            "derived_title": "Test Title",
            "source_id": "src-123",
            "local_dir": "2026-08-16-test-title",
        }

        async def dummy_gen(*args, **kwargs):
            if False:
                yield None

        mock_create_jobs.return_value = dummy_gen()
        mock_research.return_value = AsyncMock()
        mock_cover.return_value = AsyncMock()

        from podcaster.config import (
            DeepDiveArticleConfig,
            ImporterConfig,
            NativeImporterConfig,
        )
        from podcaster.workflows.deep_dive_article import workflow as dd_wf

        wf_config = DeepDiveArticleConfig(
            podcast_generator=PodcastGenerationConfig(languages=["EN"]),
            importer=ImporterConfig(native=NativeImporterConfig()),
        )

        await dd_wf.run(
            wf_config=wf_config,
            preset_name="default",
            title="Test Title",
            source_file="test.pdf",
            length="default",
            languages=["EN", "Es-ES"],
            enrich_web=False,
            generate_cover=False,
            podcast_dir=str(tmp_path),
        )

        # Check that create_podcast_audio_jobs received lowercased languages
        assert mock_create_jobs.call_count == 1
        call_args = mock_create_jobs.call_args
        assert call_args[0][2] == ["en", "es-es"]
