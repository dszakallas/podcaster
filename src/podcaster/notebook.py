import logging

from notebooklm.exceptions import NotebookNotFoundError

from .config import ImporterConfig
from .importers import import_source
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
    title: str | None = None,
) -> str:
    """Uploads a source file or URL and waits for processing."""
    res = await import_source(
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
    title: str | None = None,
    notebook_id: str | None = None,
    from_source: str | None = None,
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
    title: str | None,
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
            await _safe_delete_notebook(client, notebook_id)
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
        await _safe_delete_notebook(client, notebook_id)
        raise


async def _safe_delete_notebook(
    client: RetryingNotebookLMClient, notebook_id: str
) -> None:
    """Delete a remote notebook safely without throwing errors."""
    try:
        await client.notebooks.delete(notebook_id)
        logger.info("Remote notebook deleted successfully.")
    except Exception as delete_error:
        logger.error(f"Failed to delete remote notebook: {delete_error}")


async def _derive_notebook_title(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    source_id: str,
    title: str | None,
    current_title: str | None,
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
    title: str | None,
    notebook_id: str | None,
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
        notebook = await client.notebooks.create(title or FALLBACK_NOTEBOOK_TITLE)

    if not notebook.created_at:
        logger.debug(f"Fetching full details for notebook {notebook.id}...")
        notebook = await client.notebooks.get(notebook.id)

    return {
        "notebook_id": notebook.id,
        "created_at": notebook.created_at.isoformat() if notebook.created_at else None,
        "derived_title": notebook.title,
        "source_id": None,
    }


async def init_topic_notebook(
    client: RetryingNotebookLMClient,
    topic: str,
    title: str | None = None,
    notebook_id: str | None = None,
) -> dict:
    """Initialize a notebook for topic-based workflows with an initial seed text source."""
    if notebook_id:
        return await _get_or_create_notebook(
            client, title=title, notebook_id=notebook_id
        )

    initial_title = title or "Topic Podcast"
    logger.info("Creating remote notebook for topic...")
    notebook = await client.notebooks.create(initial_title)
    nb_id = notebook.id
    logger.info(f"Created remote notebook: {nb_id}")

    try:
        if not notebook.created_at:
            try:
                notebook = await client.notebooks.get(nb_id)
            except Exception as exc:
                logger.warning(f"Failed to fetch notebook details: {exc}")

        source_obj = await client.sources.add_text(
            nb_id,
            "Topic Brief",
            topic,
            wait=True,
        )
        source_id = source_obj.id

        derived_title = title
        if not derived_title:
            derived_title = await _derive_notebook_title(
                client, nb_id, source_id, title=title, current_title=notebook.title
            )

        return {
            "notebook_id": nb_id,
            "created_at": (
                notebook.created_at.isoformat() if notebook.created_at else None
            ),
            "derived_title": derived_title,
            "source_id": source_id,
        }
    except Exception:
        logger.error(
            f"Topic notebook initialization failed. Cleaning up remote notebook {nb_id}..."
        )
        await _safe_delete_notebook(client, nb_id)
        raise
