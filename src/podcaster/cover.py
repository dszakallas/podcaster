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


class CoverJobTerminalError(RuntimeError):
    """Raised when a cover generation batch job has permanently failed or produced no image."""


COVER_PROMPT_TEMPLATE = (
    "Based on this notebook summary, create a prompt for generating a podcast album cover. "
    "The title of the podcast is '{{ title }}'. Please ensure the prompt instructs the generator "
    "to include the text '{{ title }}' clearly as the title on the cover. "
    "Focus on symbolic, thematic, or conceptual visual metaphors (such as technology, abstract geometry, "
    "objects, atmosphere, scenery). Avoid depictions of specific real people, recognizable politicians, "
    "violence, or sensitive conflict to comply with image safety policies. "
    "Summary: {{ summary }}. "
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
    batch_job: types.BatchJob = await genai_client.batches.create(
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
        task_id=batch_job.name or "",
        image_gen_prompt=prompt_str,
        status=TaskStatus.PENDING,
    )


async def poll_cover_jobs(
    tasks: AsyncIterable[CoverTask],
) -> AsyncGenerator[CoverTask, None]:
    genai_client = genai.Client().aio

    async def check_status(t: CoverTask) -> PollStatus:
        logger.debug(f"Polling batch job: {t.task_id}")
        job: types.BatchJob = await retry_rpc(
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


def _extract_cover_image_bytes(inlined_res: types.InlinedResponse) -> bytes:
    if inlined_res.error:
        raise CoverJobTerminalError(
            f"Batch job inlined response error: {inlined_res.error}"
        )

    response = inlined_res.response
    if not response:
        raise CoverJobTerminalError(
            f"Batch job inlined response has no response object (error: {inlined_res.error})"
        )

    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise CoverJobTerminalError(
            f"Cover generation prompt was blocked: {response.prompt_feedback.block_reason}"
        )

    if not response.candidates:
        raise CoverJobTerminalError("Cover generation returned no candidates.")

    candidate = response.candidates[0]
    finish_reason = candidate.finish_reason
    finish_message = candidate.finish_message

    content = candidate.content
    parts = content.parts if content else None
    refusal_texts: list[str] = []
    if parts:
        for part in parts:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
            if part.text:
                refusal_texts.append(part.text)

    details: list[str] = []
    if finish_reason:
        details.append(f"finish_reason={finish_reason}")
    if finish_message:
        details.append(f"finish_message={finish_message}")
    if refusal_texts:
        details.append(f"refusal={' '.join(refusal_texts)}")
    if not details:
        details.append("no image data in response")
    raise CoverJobTerminalError(
        f"Cover generation failed to produce an image: {', '.join(details)}"
    )


def _save_cover_image(img_bytes: bytes, working_dir: str) -> str:
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
        raise CoverJobTerminalError(f"Generated cover image is invalid: {e}") from e

    return cover_path


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

        job: types.BatchJob = await retry_rpc(
            genai_client.batches.get, name=t.task_id, logger=logger
        )
        if not job.dest or not job.dest.inlined_responses:
            raise RuntimeError(
                "Batch job succeeded but no results found in inlined_responses."
            )

        img_bytes = _extract_cover_image_bytes(job.dest.inlined_responses[0])
        cover_path = _save_cover_image(img_bytes, working_dir)
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
        raise CoverJobTerminalError(
            f"Cover generation failed: {completed_task.error if completed_task else 'Unknown error'}"
        )

    # 3. Download cover result
    downloaded = None
    async for d in download_cover_jobs(async_iter(completed_task), working_dir):
        downloaded = d

    if not downloaded or not downloaded.cover_path:
        raise RuntimeError("Failed to download generated cover image.")

    return downloaded.cover_path
