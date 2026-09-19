import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from notebooklm.exceptions import NotebookNotFoundError

from podcaster.config import ImporterConfig, NativeImporterConfig
from podcaster.notebook import (
    FALLBACK_NOTEBOOK_TITLE,
    _derive_notebook_title,
    _safe_delete_notebook,
    get_notebook_url,
    init_notebook,
    init_topic_notebook,
    upload_source,
)


def test_get_notebook_url():
    assert (
        get_notebook_url("abc-123") == "https://notebooklm.google.com/notebook/abc-123"
    )


@pytest.mark.anyio
async def test_upload_source_success():
    client = MagicMock()
    importer = ImporterConfig(native=NativeImporterConfig())

    with patch(
        "podcaster.notebook.import_source",
        new=AsyncMock(return_value={"source_id": "src_456"}),
    ) as mock_import:
        source_id = await upload_source(
            notebook_id="nb_1",
            source_url="https://example.com/art",
            importer=importer,
            client=client,
            title="My Article",
        )
        assert source_id == "src_456"
        mock_import.assert_awaited_once_with(
            notebook_id="nb_1",
            source="https://example.com/art",
            title="My Article",
            importer=importer,
            client=client,
        )


@pytest.mark.anyio
async def test_upload_source_failure():
    client = MagicMock()
    importer = ImporterConfig(native=NativeImporterConfig())

    with (
        patch(
            "podcaster.notebook.import_source",
            new=AsyncMock(return_value={"error": "Parse error"}),
        ),
        pytest.raises(RuntimeError, match="Failed to import source: .* Parse error"),
    ):
        await upload_source(
            notebook_id="nb_1",
            source_url="https://example.com/art",
            importer=importer,
            client=client,
        )


@pytest.mark.anyio
async def test_init_notebook_requires_title_or_id_or_source():
    client = MagicMock()
    importer = ImporterConfig(native=NativeImporterConfig())

    with pytest.raises(
        ValueError,
        match="Either title or notebook_id must be provided when not initializing from source",
    ):
        await init_notebook(importer=importer, client=client)


@pytest.mark.anyio
async def test_init_notebook_existing_id_found():
    client = MagicMock()
    now = datetime.datetime.now(datetime.UTC)
    mock_nb = MagicMock(id="nb_existing", title="Existing Title", created_at=now)
    client.notebooks.get = AsyncMock(return_value=mock_nb)
    importer = ImporterConfig(native=NativeImporterConfig())

    res = await init_notebook(
        importer=importer,
        client=client,
        notebook_id="nb_existing",
    )
    assert res == {
        "notebook_id": "nb_existing",
        "created_at": now.isoformat(),
        "derived_title": "Existing Title",
        "source_id": None,
    }
    client.notebooks.get.assert_awaited_once_with("nb_existing")


@pytest.mark.anyio
async def test_init_notebook_existing_id_not_found():
    client = MagicMock()
    client.notebooks.get = AsyncMock(side_effect=NotebookNotFoundError("nb_missing"))
    importer = ImporterConfig(native=NativeImporterConfig())

    with pytest.raises(ValueError, match="Notebook nb_missing not found"):
        await init_notebook(
            importer=importer,
            client=client,
            notebook_id="nb_missing",
        )


@pytest.mark.anyio
async def test_init_notebook_with_title_only():
    client = MagicMock()
    now = datetime.datetime.now(datetime.UTC)
    mock_nb = MagicMock(id="nb_new", title="New Notebook", created_at=now)
    client.notebooks.create = AsyncMock(return_value=mock_nb)
    importer = ImporterConfig(native=NativeImporterConfig())

    res = await init_notebook(
        importer=importer,
        client=client,
        title="New Notebook",
    )
    assert res == {
        "notebook_id": "nb_new",
        "created_at": now.isoformat(),
        "derived_title": "New Notebook",
        "source_id": None,
    }
    client.notebooks.create.assert_awaited_once_with("New Notebook")


@pytest.mark.anyio
async def test_init_notebook_from_source_success():
    client = MagicMock()
    now = datetime.datetime.now(datetime.UTC)
    created_nb = MagicMock(id="nb_src", title=None, created_at=None)
    fetched_nb = MagicMock(id="nb_src", title="Fetched Title", created_at=now)
    client.notebooks.create = AsyncMock(return_value=created_nb)
    client.notebooks.get = AsyncMock(return_value=fetched_nb)
    client.notebooks.rename = AsyncMock()

    importer = ImporterConfig(native=NativeImporterConfig())

    with patch(
        "podcaster.notebook.upload_source",
        new=AsyncMock(return_value="src_123"),
    ) as mock_upload:
        res = await init_notebook(
            importer=importer,
            client=client,
            from_source="https://example.com/post",
            title="Explicit Title",
        )

        assert res == {
            "notebook_id": "nb_src",
            "created_at": now.isoformat(),
            "derived_title": "Explicit Title",
            "source_id": "src_123",
        }
        mock_upload.assert_awaited_once_with(
            "nb_src",
            "https://example.com/post",
            title="Explicit Title",
            importer=importer,
            client=client,
        )


@pytest.mark.anyio
async def test_init_notebook_from_source_upload_failure_cleans_up():
    client = MagicMock()
    created_nb = MagicMock(id="nb_fail", title="", created_at=None)
    client.notebooks.create = AsyncMock(return_value=created_nb)
    client.notebooks.get = AsyncMock(return_value=created_nb)
    client.notebooks.delete = AsyncMock()

    importer = ImporterConfig(native=NativeImporterConfig())

    with patch(
        "podcaster.notebook.upload_source",
        new=AsyncMock(side_effect=RuntimeError("Source upload failed")),
    ):
        with pytest.raises(
            RuntimeError,
            match="Failed to initialize notebook: first source upload failed",
        ):
            await init_notebook(
                importer=importer,
                client=client,
                from_source="https://example.com/post",
            )

        client.notebooks.delete.assert_awaited_once_with("nb_fail")


@pytest.mark.anyio
async def test_safe_delete_notebook_swallows_exception():
    client = MagicMock()
    client.notebooks.delete = AsyncMock(side_effect=RuntimeError("Deletion error"))
    # Should not raise
    await _safe_delete_notebook(client, "nb_fail")
    client.notebooks.delete.assert_awaited_once_with("nb_fail")


@pytest.mark.anyio
async def test_derive_notebook_title_chat_suggestion():
    client = MagicMock()
    client.notebooks.get = AsyncMock(return_value=MagicMock(title=None))
    client.chat.ask = AsyncMock(
        return_value=MagicMock(answer='  "The Future of AI"  \n')
    )
    client.notebooks.rename = AsyncMock()

    derived = await _derive_notebook_title(
        client=client,
        notebook_id="nb_chat",
        source_id="src_1",
        title=None,
        current_title=None,
    )
    assert derived == "The Future of AI"
    client.notebooks.rename.assert_awaited_once_with("nb_chat", "The Future of AI")


@pytest.mark.anyio
async def test_derive_notebook_title_fallback_when_chat_fails():
    client = MagicMock()
    client.notebooks.get = AsyncMock(side_effect=Exception("network error"))
    client.chat.ask = AsyncMock(side_effect=Exception("llm error"))
    client.notebooks.rename = AsyncMock()

    derived = await _derive_notebook_title(
        client=client,
        notebook_id="nb_fallback",
        source_id="src_1",
        title=None,
        current_title=None,
    )
    assert derived == FALLBACK_NOTEBOOK_TITLE
    client.notebooks.rename.assert_awaited_once_with(
        "nb_fallback", FALLBACK_NOTEBOOK_TITLE
    )


@pytest.mark.anyio
async def test_init_topic_notebook_with_existing_id():
    client = MagicMock()
    now = datetime.datetime.now(datetime.UTC)
    mock_nb = MagicMock(id="nb_topic_exist", title="Topic NB", created_at=now)
    client.notebooks.get = AsyncMock(return_value=mock_nb)

    res = await init_topic_notebook(
        client=client,
        topic="Machine Learning",
        notebook_id="nb_topic_exist",
    )
    assert res["notebook_id"] == "nb_topic_exist"
    assert res["derived_title"] == "Topic NB"
    client.notebooks.get.assert_awaited_once_with("nb_topic_exist")


@pytest.mark.anyio
async def test_init_topic_notebook_creates_and_adds_text():
    client = MagicMock()
    now = datetime.datetime.now(datetime.UTC)
    mock_nb = MagicMock(id="nb_topic_new", title="Topic Podcast", created_at=now)
    client.notebooks.create = AsyncMock(return_value=mock_nb)
    client.sources.add_text = AsyncMock(return_value=MagicMock(id="src_seed"))
    client.notebooks.rename = AsyncMock()

    res = await init_topic_notebook(
        client=client,
        topic="Quantum Computing",
        title="Custom Quantum Title",
    )
    assert res["notebook_id"] == "nb_topic_new"
    assert res["derived_title"] == "Custom Quantum Title"
    assert res["source_id"] == "src_seed"
    client.notebooks.create.assert_awaited_once_with("Custom Quantum Title")
    client.sources.add_text.assert_awaited_once_with(
        "nb_topic_new",
        "Topic Brief",
        "Quantum Computing",
        wait=True,
    )


@pytest.mark.anyio
async def test_init_topic_notebook_failure_cleans_up():
    client = MagicMock()
    mock_nb = MagicMock(id="nb_topic_fail", title="Topic Podcast", created_at=None)
    client.notebooks.create = AsyncMock(return_value=mock_nb)
    client.notebooks.get = AsyncMock(return_value=mock_nb)
    client.sources.add_text = AsyncMock(side_effect=RuntimeError("API quota"))
    client.notebooks.delete = AsyncMock()

    with pytest.raises(RuntimeError, match="API quota"):
        await init_topic_notebook(
            client=client,
            topic="Space Exploration",
        )
    client.notebooks.delete.assert_awaited_once_with("nb_topic_fail")
