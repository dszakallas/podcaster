import logging
from typing import Optional

from notebooklm.exceptions import NotebookNotFoundError

from .config import ImporterConfig
from .utils.notebooklm import RetryingNotebookLMClient

logger = logging.getLogger(__name__)

FALLBACK_NOTEBOOK_TITLE = "Notebook"
TITLE_SUGGESTION_PROMPT = (
    "Based on the uploaded source, suggest a concise, catchy, and professional title for this notebook/podcast. "
    "Do not include any markdown, formatting, introductory or concluding text, or any citations. Respond ONLY with the plain text suggested title."
)


def get_notebook_url(notebook_id: str) -> str:
    """Return the standard NotebookLM web URL for a notebook ID."""
    return f"https://notebooklm.google.com/notebook/{notebook_id}"


async def upload_source(
    notebook_id: str,
    source_url: str,
    importer: ImporterConfig,
    client: RetryingNotebookLMClient,
    title: Optional[str] = None,
) -> str:
    """Uploads a source file or URL and waits for processing."""
    from . import research

    res = await research.import_source(
        notebook_id=notebook_id,
        source=source_url,
        title=title,
        importer=importer,
        client=client,
    )
    source_id = res.get("source_id")
    if not source_id:
        error_msg = res.get("error") or "Unknown error"
        raise RuntimeError(f"Failed to import source: {source_url}. Error: {error_msg}")
    return source_id


async def init_notebook(
    importer: ImporterConfig,
    client: RetryingNotebookLMClient,
    title: Optional[str] = None,
    notebook_id: Optional[str] = None,
    from_source: Optional[str] = None,
) -> dict:
    """Initialize a notebook from an existing ID, title, or first source."""
    if from_source:
        return await _create_notebook_from_source(
            client=client,
            importer=importer,
            from_source=from_source,
            title=title,
        )
    return await _get_or_create_notebook(
        client=client, title=title, notebook_id=notebook_id
    )


async def _create_notebook_from_source(
    client: RetryingNotebookLMClient,
    importer: ImporterConfig,
    from_source: str,
    title: Optional[str],
) -> dict:
    """Create a notebook, upload its first source, and derive its title."""
    try:
        logger.info("Creating remote notebook...")
        notebook = await client.notebooks.create(title or "")
        notebook_id = notebook.id
        logger.info(f"Created remote notebook: {notebook_id}")
    except Exception as exc:
        logger.error(f"Failed to create remote notebook: {exc}")
        raise

    try:
        if not notebook.created_at:
            try:
                notebook = await client.notebooks.get(notebook_id)
            except Exception as exc:
                logger.warning(f"Failed to fetch notebook details: {exc}")

        try:
            source_id = await upload_source(
                notebook_id,
                from_source,
                title=title,
                importer=importer,
                client=client,
            )
        except Exception as exc:
            logger.error(
                "Failed to upload first source. Cleaning up remote notebook %s...",
                notebook_id,
            )
            try:
                await client.notebooks.delete(notebook_id)
                logger.info("Remote notebook deleted successfully.")
            except Exception as delete_error:
                logger.error(f"Failed to delete remote notebook: {delete_error}")
            raise RuntimeError(
                f"Failed to initialize notebook: first source upload failed. {exc}"
            ) from exc

        derived_title = await _derive_notebook_title(
            client, notebook_id, source_id, title, notebook.title
        )
        return {
            "notebook_id": notebook_id,
            "created_at": (
                notebook.created_at.isoformat() if notebook.created_at else None
            ),
            "derived_title": derived_title,
            "source_id": source_id,
        }
    except Exception:
        raise
    except BaseException:
        logger.error(
            f"Notebook initialization interrupted. Cleaning up remote notebook {notebook_id}..."
        )
        try:
            await client.notebooks.delete(notebook_id)
            logger.info("Remote notebook deleted successfully.")
        except Exception as delete_error:
            logger.error(f"Failed to delete remote notebook: {delete_error}")
        raise


async def _derive_notebook_title(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    source_id: str,
    title: Optional[str],
    current_title: Optional[str],
) -> str:
    """Return an explicit, notebook-provided, generated, or fallback title."""
    if title:
        return title

    derived_title = current_title
    if not derived_title:
        try:
            notebook = await client.notebooks.get(notebook_id)
            derived_title = notebook.title
        except Exception as exc:
            logger.warning(f"Failed to re-fetch notebook title: {exc}")

    if not derived_title:
        try:
            logger.info("Prompting notebook to generate a title...")
            chat_res = await client.chat.ask(
                notebook_id,
                TITLE_SUGGESTION_PROMPT,
                source_ids=[source_id],
            )
            derived_title = chat_res.answer.strip().strip('"').strip("'").strip()
        except Exception as exc:
            logger.warning(f"Failed to prompt notebook for a title: {exc}")

    derived_title = derived_title or FALLBACK_NOTEBOOK_TITLE
    try:
        await client.notebooks.rename(notebook_id, derived_title)
        logger.info(f"Renamed remote notebook to derived title: '{derived_title}'")
    except Exception as exc:
        logger.warning(f"Failed to rename remote notebook to '{derived_title}': {exc}")
    return derived_title


async def _get_or_create_notebook(
    client: RetryingNotebookLMClient,
    title: Optional[str],
    notebook_id: Optional[str],
) -> dict:
    """Fetch an existing notebook or create an empty one."""
    if not title and not notebook_id:
        raise ValueError(
            "Either title or notebook_id must be provided when not initializing from source."
        )

    if notebook_id:
        logger.debug(f"Fetching existing notebook: {notebook_id}")
        try:
            notebook = await client.notebooks.get(notebook_id)
        except NotebookNotFoundError as exc:
            raise ValueError(f"Notebook {notebook_id} not found") from exc
    else:
        logger.debug(f"Creating notebook: {title}")
        notebook = await client.notebooks.create(title)

    if not notebook.created_at:
        logger.debug(f"Fetching full details for notebook {notebook.id}...")
        notebook = await client.notebooks.get(notebook.id)

    return {
        "notebook_id": notebook.id,
        "created_at": notebook.created_at.isoformat() if notebook.created_at else None,
        "derived_title": notebook.title,
        "source_id": None,
    }
