import logging
import os
from typing import Optional

from notebooklm.exceptions import NotebookNotFoundError

from .config import ImporterConfig
from .utils import (
    get_notebooklm_client,
    get_or_create_notebook_dir,
)

logger = logging.getLogger(__name__)

FALLBACK_NOTEBOOK_TITLE = "Notebook"
TITLE_SUGGESTION_PROMPT = (
    "Based on the uploaded source, suggest a concise, catchy, and professional title for this notebook/podcast. "
    "Do not include any markdown, formatting, introductory or concluding text, or any citations. Respond ONLY with the plain text suggested title."
)


async def upload_source(
    notebook_id: str,
    source_file: str,
    importer: ImporterConfig,
    title: Optional[str] = None,
) -> str:
    """Uploads a source file or URL and waits for processing."""
    from . import research

    res = await research.import_source(
        notebook_id, source_file, importer=importer, title=title
    )
    if "error" in res and res.get("error"):
        raise RuntimeError(
            f"Failed to import source: {source_file}. Error: {res['error']}"
        )
    source_id = res.get("source_id")
    if not source_id:
        raise RuntimeError(f"Failed to obtain source ID for: {source_file}")
    return source_id


async def init_notebook(
    podcast_dir: str,
    importer: ImporterConfig,
    title: Optional[str] = None,
    notebook_id: Optional[str] = None,
    from_source: Optional[str] = None,
) -> dict:
    """Initialize a notebook: creates/fetches a notebook and initializes its local directory.

    If from_source is provided, it uploads that file as the first source before creating the local
    directory, deriving the title after successful upload. If upload fails, the remote notebook
    is cleaned up (deleted) and an error is raised.
    """

    if from_source:
        async with get_notebooklm_client() as client:
            try:
                logger.info("Creating remote notebook...")
                notebook = await client.notebooks.create(title or "")
                created_notebook_id = notebook.id
                logger.info(f"Created remote notebook: {created_notebook_id}")
            except Exception as e:
                logger.error(f"Failed to create remote notebook: {e}")
                raise e

            try:
                # Re-fetch to get created_at if it was None
                if not notebook.created_at:
                    try:
                        notebook = await client.notebooks.get(created_notebook_id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch notebook details: {e}")

                # Upload the first source
                try:
                    source_id = await upload_source(
                        created_notebook_id,
                        from_source,
                        title=title,
                        importer=importer,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to upload first source. Cleaning up remote notebook {created_notebook_id}..."
                    )
                    try:
                        await client.notebooks.delete(created_notebook_id)
                        logger.info("Remote notebook deleted successfully.")
                    except Exception as delete_err:
                        logger.error(f"Failed to delete remote notebook: {delete_err}")
                    raise RuntimeError(
                        f"Failed to initialize notebook: first source upload failed. {e}"
                    ) from e

                # Derive title if not provided
                derived_title = title
                if not derived_title:
                    # Re-fetch notebook details again to see if title got populated
                    try:
                        notebook = await client.notebooks.get(created_notebook_id)
                        derived_title = notebook.title
                    except Exception as e:
                        logger.warning(f"Failed to re-fetch notebook title: {e}")

                    # If still empty, prompt the notebook to suggest a title based on the uploaded source
                    if not derived_title:
                        try:
                            logger.info("Prompting notebook to generate a title...")
                            chat_res = await client.chat.ask(
                                created_notebook_id,
                                TITLE_SUGGESTION_PROMPT,
                                source_ids=[source_id],
                            )
                            derived_title = (
                                chat_res.answer.strip().strip('"').strip("'").strip()
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to prompt notebook for a title: {e}"
                            )

                    if not derived_title:
                        derived_title = FALLBACK_NOTEBOOK_TITLE

                    # Update remote notebook title with derived title so it's not empty
                    try:
                        await client.notebooks.rename(
                            created_notebook_id, derived_title
                        )
                        logger.info(
                            f"Renamed remote notebook to derived title: '{derived_title}'"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to rename remote notebook to '{derived_title}': {e}"
                        )

                # Now create the local directory
                logger.info(
                    f"Initializing directory for notebook {created_notebook_id} (title: '{derived_title}') in {podcast_dir}..."
                )
                notebook_dir = get_or_create_notebook_dir(
                    podcast_dir, created_notebook_id, derived_title, notebook.created_at
                )

                return {
                    "notebook_id": created_notebook_id,
                    "created_at": (
                        notebook.created_at.isoformat() if notebook.created_at else None
                    ),
                    "local_dir": os.path.basename(notebook_dir),
                    "derived_title": derived_title,
                    "source_id": source_id,
                }
            except Exception:
                raise
            except BaseException:
                logger.error(
                    f"Notebook initialization interrupted. Cleaning up remote notebook {created_notebook_id}..."
                )
                try:
                    await client.notebooks.delete(created_notebook_id)
                    logger.info("Remote notebook deleted successfully.")
                except Exception as delete_err:
                    logger.error(f"Failed to delete remote notebook: {delete_err}")
                raise
    else:
        if not title and not notebook_id:
            raise ValueError(
                "Either title or notebook_id must be provided when not initializing from source."
            )

        async with get_notebooklm_client() as client:
            if notebook_id:
                logger.debug(f"Fetching existing notebook: {notebook_id}")
                try:
                    notebook = await client.notebooks.get(notebook_id)
                except NotebookNotFoundError as e:
                    raise ValueError(f"Notebook {notebook_id} not found") from e
            else:
                logger.debug(f"Creating notebook: {title}")
                notebook = await client.notebooks.create(title)

            # Re-fetch to get created_at if it was None
            if not notebook.created_at:
                logger.debug(f"Fetching full details for notebook {notebook.id}...")
                notebook = await client.notebooks.get(notebook.id)

            logger.debug(
                f"Initializing directory for notebook {notebook.id} in {podcast_dir}"
            )
            notebook_dir = get_or_create_notebook_dir(
                podcast_dir, notebook.id, notebook.title, notebook.created_at
            )

            return {
                "notebook_id": notebook.id,
                "created_at": (
                    notebook.created_at.isoformat() if notebook.created_at else None
                ),
                "local_dir": os.path.basename(notebook_dir),
                "derived_title": notebook.title,
                "source_id": None,
            }
