"""Tests for cover generation functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from podcaster.cover import create_cover_job, generate_cover_for_notebook
from podcaster.models import TaskStatus


@pytest.mark.anyio
async def test_create_cover_job_uses_given_model():
    mock_client = MagicMock()
    mock_client.notebooks.get = AsyncMock(return_value=MagicMock(title="Test"))
    mock_batch_job = MagicMock()
    mock_batch_job.name = "batch-123"

    mock_genai_batches = MagicMock()
    mock_genai_batches.create = AsyncMock(return_value=mock_batch_job)
    mock_genai_client = MagicMock()
    mock_genai_client.batches = mock_genai_batches

    with patch("podcaster.cover.genai.Client") as mock_genai:
        mock_genai.return_value.aio = mock_genai_client

        task = await create_cover_job(
            notebook_id="nb-1",
            notebooklm_client=mock_client,
            model="custom-image-model",
            image_gen_prompt="Prompt",
        )

    assert task.task_id == "batch-123"
    assert task.status == TaskStatus.PENDING
    mock_genai_batches.create.assert_called_once()
    call_kwargs = mock_genai_batches.create.call_args.kwargs
    assert call_kwargs["model"] == "custom-image-model"
    assert call_kwargs["src"][0].model == "custom-image-model"


@pytest.mark.anyio
async def test_generate_cover_for_notebook_passes_model():
    mock_client = MagicMock()
    with patch(
        "podcaster.cover.create_cover_job", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = MagicMock(
            task_id="task-1",
            image_gen_prompt="prompt",
        )
        with patch("podcaster.cover.poll_cover_jobs") as mock_poll:

            async def fake_poll(tasks):
                yield MagicMock(status=TaskStatus.COMPLETED, task_id="task-1")

            mock_poll.side_effect = fake_poll

            with patch("podcaster.cover.download_cover_jobs") as mock_download:

                async def fake_download(tasks, working_dir):
                    yield MagicMock(cover_path="/tmp/cover.jpg")

                mock_download.side_effect = fake_download

                result = await generate_cover_for_notebook(
                    notebook_id="nb-1",
                    working_dir="/tmp",
                    notebooklm_client=mock_client,
                    model="custom-model",
                )

                assert result == "/tmp/cover.jpg"
                mock_create.assert_called_once_with(
                    "nb-1",
                    mock_client,
                    model="custom-model",
                    image_gen_prompt=None,
                )
