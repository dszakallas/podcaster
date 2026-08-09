import asyncio
import logging
import os
from typing import Any, AsyncGenerator, AsyncIterable, Callable, Optional

from google import genai
from google.genai import types
from notebooklm.exceptions import NotebookNotFoundError
from PIL import Image

from .models import CoverTask, TaskStatus
from .utils import (
    get_notebooklm_client,
    get_or_create_notebook_dir,
    load_config,
    retry_rpc,
)

logger = logging.getLogger(__name__)

DEFAULT_COVER_MODEL = "gemini-3.1-flash-image"
POLL_INTERVAL_SECONDS = 15

COVER_PROMPT_TEMPLATE = (
    "Based on this notebook summary, create a prompt for generating a podcast album cover. "
    "The title of the podcast is '{{ title }}'. Please ensure the prompt instructs the generator "
    "to include the text '{{ title }}' clearly as the title on the cover. Summary: {{ summary }}. "
    "Generate ONLY one prompt, nothing else."
)


async def create_cover_job(
    notebook_id: str, image_gen_prompt: Optional[str] = None
) -> CoverTask:
    async with get_notebooklm_client() as client:
        try:
            notebook = await client.notebooks.get(notebook_id)
        except NotebookNotFoundError as e:
            raise ValueError(f"Notebook {notebook_id} not found") from e

        if not image_gen_prompt:
            from jinja2 import Template

            summary = await client.notebooks.get_summary(notebook_id)
            chat_prompt = Template(COVER_PROMPT_TEMPLATE).render(
                title=notebook.title, summary=summary
            )
            image_gen_result = await client.chat.ask(notebook_id, chat_prompt)
            image_gen_prompt = image_gen_result.answer

        prompt_str = image_gen_prompt or ""
        logger.debug(f"Generated prompt for image: {prompt_str}")

        genai_client = genai.Client()
        logger.debug("Submitting batch job for image generation...")

        batch_job: Any = await retry_rpc(
            genai_client.batches.create,
            model=DEFAULT_COVER_MODEL,
            src=[
                types.InlinedRequest(
                    model=DEFAULT_COVER_MODEL,
                    contents=[
                        types.Content(parts=[types.Part(text=prompt_str)], role="user")
                    ],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio="1:1"),
                    ),
                )
            ],
            logger=logger,
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
    genai_client = genai.Client()
    async for t in tasks:
        task_id = t.task_id
        logger.debug(f"Polling batch job: {task_id}")
        while True:
            job: Any = await retry_rpc(
                genai_client.batches.get, name=task_id, logger=logger
            )
            state_str = str(job.state)
            if "SUCCEEDED" in state_str:
                yield t.model_copy(update={"status": TaskStatus.COMPLETED})
                break
            elif "FAILED" in state_str or "CANCELLED" in state_str:
                error_msg = str(job.error) if job.error else "Job failed/cancelled"
                yield t.model_copy(
                    update={"status": TaskStatus.FAILED, "error": error_msg}
                )
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def download_cover_jobs(
    tasks: AsyncIterable[CoverTask], podcast_dir: Optional[str] = None
) -> AsyncGenerator[CoverTask, None]:
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    genai_client = genai.Client()

    async with get_notebooklm_client() as client:
        async for t in tasks:
            if t.status != TaskStatus.COMPLETED:
                logger.warning(
                    f"Skipping download for cover task {t.task_id} as status is not completed"
                )
                continue

            notebook_id = t.notebook_id
            task_id = t.task_id

            try:
                notebook = await client.notebooks.get(notebook_id)
            except NotebookNotFoundError as e:
                raise ValueError(f"Notebook {notebook_id} not found") from e

            job: Any = await retry_rpc(
                genai_client.batches.get, name=task_id, logger=logger
            )
            if not job.dest or not job.dest.inlined_responses:
                raise RuntimeError(
                    "Batch job succeeded but no results found in inlined_responses."
                )

            inlined_res = job.dest.inlined_responses[0]
            if inlined_res.error:
                raise RuntimeError(
                    f"Batch job inlined response error: {inlined_res.error}"
                )

            img_bytes = (
                inlined_res.response.candidates[0].content.parts[0].inline_data.data
            )

            notebook_dir = get_or_create_notebook_dir(
                podcast_dir,
                notebook_id,
                notebook.title if notebook else "NotebookLM Podcast",
                notebook.created_at if notebook else None,
            )

            cover_path = os.path.join(notebook_dir, "cover.jpg")

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
    podcast_dir: Optional[str] = None,
    task_id: Optional[str] = None,
    image_gen_prompt: Optional[str] = None,
    on_start_callback: Optional[Callable[[str, str], Any]] = None,
) -> str:
    # 1. Start cover job if not provided
    if not task_id:
        task = await create_cover_job(notebook_id, image_gen_prompt)
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
    async def task_gen():
        yield task_obj

    completed_task = None
    async for t in poll_cover_jobs(task_gen()):
        completed_task = t

    if not completed_task or completed_task.status != TaskStatus.COMPLETED:
        raise RuntimeError(
            f"Cover generation failed: {completed_task.error if completed_task else 'Unknown error'}"
        )

    # 3. Download cover result
    async def completed_gen():
        yield completed_task

    downloaded = None
    async for d in download_cover_jobs(completed_gen(), podcast_dir):
        downloaded = d

    if not downloaded or not downloaded.cover_path:
        raise RuntimeError("Failed to download generated cover image.")

    return downloaded.cover_path
