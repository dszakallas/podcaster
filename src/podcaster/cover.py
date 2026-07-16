import asyncio
import io
import logging
import os
import time
from typing import Any, AsyncGenerator, Callable, Optional

from google import genai
from google.genai import types
from notebooklm.exceptions import NotebookNotFoundError
from PIL import Image

from .utils import (
    RetryingNotebookLMClient,
    get_or_create_notebook_dir,
    get_storage_path,
    load_config,
    retry_rpc,
)

logger = logging.getLogger(__name__)


async def create_cover_job(
    notebook_id: str, image_gen_prompt: Optional[str] = None
) -> dict:
    storage_path = get_storage_path()
    async with await RetryingNotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        try:
            notebook = await client.notebooks.get(notebook_id)
        except NotebookNotFoundError as e:
            raise ValueError(f"Notebook {notebook_id} not found") from e

        if not image_gen_prompt:
            summary = await client.notebooks.get_summary(notebook_id)
            chat_prompt = f"Based on this notebook summary, create a prompt for generating a podcast album cover. The title of the podcast is '{notebook.title}'. Please ensure the prompt instructs the generator to include the text '{notebook.title}' clearly as the title on the cover. Summary: {summary}. Generate ONLY one prompt, nothing else."
            image_gen_result = await client.chat.ask(notebook_id, chat_prompt)
            image_gen_prompt = image_gen_result.answer

        logger.debug(f"Generated prompt for image: {image_gen_prompt}")

        genai_client = genai.Client()
        logger.debug("Submitting batch job for image generation...")

        batch_job = await retry_rpc(
            genai_client.batches.create,
            model="gemini-3.1-flash-image",
            src=[
                types.InlinedRequest(
                    model="gemini-3.1-flash-image",
                    contents=[
                        types.Content(
                            parts=[types.Part(text=image_gen_prompt)], role="user"
                        )
                    ],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio="1:1"),
                    ),
                )
            ],
            logger=logger,
        )

        return {
            "notebook_id": notebook_id,
            "task_id": batch_job.name,
            "image_gen_prompt": image_gen_prompt,
            "status": "pending",
            "type": "cover",
        }


async def poll_cover_jobs(
    tasks: AsyncGenerator[dict, None],
    retry_count: int = 0,
) -> AsyncGenerator[dict, None]:
    genai_client = genai.Client()
    async for task in tasks:
        current_task_id = task["task_id"]
        logger.debug(f"Polling batch job: {current_task_id}")
        attempts = 0
        while True:
            job = await retry_rpc(
                genai_client.batches.get, name=current_task_id, logger=logger
            )
            state_str = str(job.state)
            if "SUCCEEDED" in state_str:
                yield {**task, "task_id": current_task_id, "status": "completed"}
                break
            elif "FAILED" in state_str or "CANCELLED" in state_str:
                error_msg = str(job.error) if job.error else "Job failed/cancelled"
                if attempts < retry_count:
                    attempts += 1
                    logger.warning(
                        f"Cover generation job {current_task_id} failed: {error_msg}. Retrying (attempt {attempts}/{retry_count})...."
                    )
                    image_gen_prompt = task.get("image_gen_prompt")
                    if not image_gen_prompt:
                        logger.error(
                            "No image_gen_prompt found in task to retry. Failing."
                        )
                        yield {
                            **task,
                            "task_id": current_task_id,
                            "status": "failed",
                            "error": error_msg,
                        }
                        break

                    try:
                        new_batch_job = await retry_rpc(
                            genai_client.batches.create,
                            model="gemini-3.1-flash-image",
                            src=[
                                types.InlinedRequest(
                                    model="gemini-3.1-flash-image",
                                    contents=[
                                        types.Content(
                                            parts=[types.Part(text=image_gen_prompt)],
                                            role="user",
                                        )
                                    ],
                                    config=types.GenerateContentConfig(
                                        response_modalities=["IMAGE"],
                                        image_config=types.ImageConfig(
                                            aspect_ratio="1:1"
                                        ),
                                    ),
                                )
                            ],
                            logger=logger,
                        )
                        current_task_id = new_batch_job.name
                        logger.info(f"Started retry cover job: {current_task_id}")
                        continue
                    except Exception as re:
                        logger.error(f"Failed to submit retry job: {re}")
                        yield {
                            **task,
                            "task_id": current_task_id,
                            "status": "failed",
                            "error": f"{error_msg} (Retry submit failed: {re})",
                        }
                        break
                else:
                    yield {
                        **task,
                        "task_id": current_task_id,
                        "status": "failed",
                        "error": error_msg,
                    }
                    break
            await asyncio.sleep(15)


async def download_cover_jobs(
    tasks: AsyncGenerator[dict, None], podcast_dir: Optional[str] = None
) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    genai_client = genai.Client()

    async with await RetryingNotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        async for task in tasks:
            if task.get("status") != "completed":
                logger.warning(
                    f"Skipping download for cover task {task['task_id']} as status is not completed"
                )
                continue

            notebook_id = task["notebook_id"]
            task_id = task["task_id"]

            try:
                notebook = await client.notebooks.get(notebook_id)
            except NotebookNotFoundError as e:
                raise ValueError(f"Notebook {notebook_id} not found") from e

            job = await retry_rpc(genai_client.batches.get, name=task_id, logger=logger)
            if not job.dest or not job.dest.inlined_responses:
                raise RuntimeError(
                    "Batch job succeeded but no results found in inlined_responses."
                )

            inlined_res = job.dest.inlined_responses[0]
            if inlined_res.error:
                raise RuntimeError(
                    f"Batch job inlined response error: {inlined_res.error}"
                )

            response = inlined_res.response
            if (
                not response
                or not response.candidates
                or not response.candidates[0].content.parts
            ):
                raise RuntimeError("Batch job response is empty or has no candidates.")

            image_part = response.candidates[0].content.parts[0]
            if not image_part.inline_data:
                raise RuntimeError(
                    "Batch job response part does not contain inline_data."
                )

            image_bytes = image_part.inline_data.data
            image = Image.open(io.BytesIO(image_bytes))

            notebook_dir = get_or_create_notebook_dir(
                podcast_dir, notebook_id, notebook.title, notebook.created_at
            )

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"album_cover_{timestamp}.png"
            out_path = os.path.join(notebook_dir, filename)
            image.save(out_path)

            yield {
                **task,
                "path": out_path,
                "filename": filename,
                "status": "downloaded",
            }


async def generate_cover(
    notebook_id: str,
    podcast_dir: Optional[str] = None,
    task_id: Optional[str] = None,
    image_gen_prompt: Optional[str] = None,
    on_start_callback: Optional[Callable[[str, str], Any]] = None,
    retry_count: int = 0,
) -> str:
    # 1. Start cover job if not provided
    if not task_id:
        task = await create_cover_job(notebook_id, image_gen_prompt)
        task_id = task["task_id"]
        image_gen_prompt = task["image_gen_prompt"]
        if on_start_callback:
            await on_start_callback(task_id, image_gen_prompt)

    # 2. Poll cover job
    async def task_gen():
        yield {
            "task_id": task_id,
            "notebook_id": notebook_id,
            "image_gen_prompt": image_gen_prompt,
        }

    completed_task = None
    async for t in poll_cover_jobs(task_gen(), retry_count=retry_count):
        completed_task = t

    if not completed_task or completed_task.get("status") != "completed":
        raise RuntimeError(
            f"Cover generation failed: {completed_task.get('error') if completed_task else 'Unknown error'}"
        )

    # 3. Download cover result
    async def completed_gen():
        yield completed_task

    downloaded = None
    async for d in download_cover_jobs(completed_gen(), podcast_dir):
        downloaded = d

    if not downloaded:
        raise RuntimeError("Failed to download cover image.")

    return downloaded["path"]
