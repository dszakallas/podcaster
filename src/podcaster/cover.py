import asyncio
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from typing import Any

from google import genai
from google.genai import types
from notebooklm.exceptions import NotebookNotFoundError
from PIL import Image

from .models import CoverTask, TaskStatus
from .utils import PollingJob, PollStatus, async_iter
from .utils.notebooklm import RetryingNotebookLMClient
from .utils.retry import retry_rpc

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15.0
MAX_POLL_TIMEOUT_SECONDS = 1800.0

COVER_PROMPT_TEMPLATE = (
    "Based on this notebook summary, create a prompt for generating a podcast album cover. "
    "The title of the podcast is '{{ title }}'. Please ensure the prompt instructs the generator "
    "to include the text '{{ title }}' clearly as the title on the cover. Summary: {{ summary }}. "
    "Generate ONLY one prompt, nothing else."
)


async def create_cover_job(
    notebook_id: str,
    notebooklm_client: RetryingNotebookLMClient,
    model: str,
    image_gen_prompt: str | None = None,
) -> CoverTask:
    try:
        notebook = await notebooklm_client.notebooks.get(notebook_id)
    except NotebookNotFoundError as e:
        raise ValueError(f"Notebook {notebook_id} not found") from e

    if not image_gen_prompt:
        from jinja2 import Template

        summary = await notebooklm_client.notebooks.get_summary(notebook_id)
        chat_prompt = Template(COVER_PROMPT_TEMPLATE).render(
            title=notebook.title, summary=summary
        )
        image_gen_result = await notebooklm_client.chat.ask(notebook_id, chat_prompt)
        image_gen_prompt = image_gen_result.answer

    prompt_str = image_gen_prompt or ""
    logger.debug(f"Generated prompt for image: {prompt_str}")

    genai_client = genai.Client().aio
    logger.debug("Submitting batch job for image generation...")

    # Creating a batch is not idempotent, so retrying it after an uncertain
    # network failure could submit duplicate image-generation jobs.
    batch_job: Any = await genai_client.batches.create(
        model=model,
        src=[
            types.InlinedRequest(
                model=model,
                contents=[
                    types.Content(parts=[types.Part(text=prompt_str)], role="user")
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="1:1"),
                ),
            )
        ],
    )

    return CoverTask(
        notebook_id=notebook_id,
        task_id=batch_job.name,
        image_gen_prompt=prompt_str,
        status=TaskStatus.PENDING,
    )


async def poll_cover_jobs(
    tasks: AsyncIterable[CoverTask],
) -> AsyncGenerator[CoverTask, None]:
    genai_client = genai.Client().aio

    async def check_status(t: CoverTask) -> PollStatus:
        logger.debug(f"Polling batch job: {t.task_id}")
        job: Any = await retry_rpc(
            genai_client.batches.get, name=t.task_id, logger=logger
        )
        state_str = str(job.state)
        if "SUCCEEDED" in state_str:
            return PollStatus(status=TaskStatus.COMPLETED)
        elif "FAILED" in state_str or "CANCELLED" in state_str:
            error_msg = str(job.error) if job.error else "Job failed/cancelled"
            return PollStatus(status=TaskStatus.FAILED, error=error_msg)
        return PollStatus(status=TaskStatus.IN_PROGRESS)

    polling_job = PollingJob(
        check_status=check_status,
        timeout=MAX_POLL_TIMEOUT_SECONDS,
        interval=POLL_INTERVAL_SECONDS,
        sleep=asyncio.sleep,
    )
    async for t in polling_job.poll_stream(tasks):
        yield t


async def download_cover_jobs(
    tasks: AsyncIterable[CoverTask], working_dir: str
) -> AsyncGenerator[CoverTask, None]:

    genai_client = genai.Client().aio

    async for t in tasks:
        if t.status != TaskStatus.COMPLETED:
            logger.warning(
                f"Skipping download for cover task {t.task_id} as status is not completed"
            )
            continue

        task_id = t.task_id

        job: Any = await retry_rpc(
            genai_client.batches.get, name=task_id, logger=logger
        )
        if not job.dest or not job.dest.inlined_responses:
            raise RuntimeError(
                "Batch job succeeded but no results found in inlined_responses."
            )

        inlined_res = job.dest.inlined_responses[0]
        if inlined_res.error:
            raise RuntimeError(f"Batch job inlined response error: {inlined_res.error}")

        img_bytes = inlined_res.response.candidates[0].content.parts[0].inline_data.data

        os.makedirs(working_dir, exist_ok=True)
        cover_path = os.path.join(working_dir, "cover.jpg")

        with open(cover_path, "wb") as f:
            f.write(img_bytes)

        try:
            with Image.open(cover_path) as img:
                img.verify()
            logger.info(f"Verified cover image saved to: {cover_path}")
        except Exception as e:
            if os.path.exists(cover_path):
                os.remove(cover_path)
            raise RuntimeError(f"Generated cover image is invalid: {e}") from e

        yield t.model_copy(update={"cover_path": cover_path})


async def generate_cover_for_notebook(
    notebook_id: str,
    working_dir: str,
    notebooklm_client: RetryingNotebookLMClient,
    model: str,
    task_id: str | None = None,
    image_gen_prompt: str | None = None,
    on_start_callback: Callable[[str, str], Any] | None = None,
) -> str:

    # 1. Start cover job if not provided
    if not task_id:
        task = await create_cover_job(
            notebook_id,
            notebooklm_client,
            model=model,
            image_gen_prompt=image_gen_prompt,
        )
        task_id = task.task_id
        image_gen_prompt = task.image_gen_prompt
        if on_start_callback:
            assert task_id is not None
            assert image_gen_prompt is not None
            cb_res = on_start_callback(task_id, image_gen_prompt)
            if asyncio.iscoroutine(cb_res) or hasattr(cb_res, "__await__"):
                await cb_res

    assert task_id is not None
    assert image_gen_prompt is not None
    task_obj = CoverTask(
        notebook_id=notebook_id,
        task_id=task_id,
        image_gen_prompt=image_gen_prompt,
    )

    # 2. Poll cover job
    completed_task = None
    async for t in poll_cover_jobs(async_iter(task_obj)):
        completed_task = t

    if not completed_task or completed_task.status != TaskStatus.COMPLETED:
        raise RuntimeError(
            f"Cover generation failed: {completed_task.error if completed_task else 'Unknown error'}"
        )

    # 3. Download cover result
    downloaded = None
    async for d in download_cover_jobs(async_iter(completed_task), working_dir):
        downloaded = d

    if not downloaded or not downloaded.cover_path:
        raise RuntimeError("Failed to download generated cover image.")

    return downloaded.cover_path
