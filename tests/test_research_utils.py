"""Unit tests for pure utility functions in podcaster.research."""

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from notebooklm.types import ResearchSource

from podcaster.config import NotebookLMConfig
from podcaster.research import (
    evaluate_importer_match,
    extract_drive_file_id,
    import_source,
    normalize_source,
    parse_summary_response,
    strip_citations,
)

# ---------------------------------------------------------------------------
# evaluate_importer_match
# ---------------------------------------------------------------------------


class TestEvaluateImporterMatch:
    def test_empty_expressions(self):
        assert evaluate_importer_match([], "anything") is False

    def test_match_all(self):
        assert evaluate_importer_match([".*"], "anything") is True

    def test_no_match(self):
        assert evaluate_importer_match(["^xyz$"], "abc") is False

    def test_negation_removes_match(self):
        exprs = [".*", "!.*wsj\\.com/.*"]
        assert evaluate_importer_match(exprs, "https://example.com/article") is True
        assert evaluate_importer_match(exprs, "https://wsj.com/article") is False

    def test_negation_on_unmatched_is_noop(self):
        exprs = ["!.*wsj\\.com/.*"]
        # Nothing matched yet, negation has no effect
        assert evaluate_importer_match(exprs, "https://example.com") is False

    def test_later_positive_overrides_negation(self):
        exprs = [".*", "!.*wsj\\.com/.*", ".*wsj\\.com/special/.*"]
        assert evaluate_importer_match(exprs, "https://wsj.com/special/article") is True

    def test_multiple_negations(self):
        exprs = [".*", "!.*wsj\\.com/.*", "!.*forbes\\.com/.*"]
        assert evaluate_importer_match(exprs, "https://example.com") is True
        assert evaluate_importer_match(exprs, "https://wsj.com/article") is False
        assert evaluate_importer_match(exprs, "https://forbes.com/article") is False

    def test_default_config_pattern(self):
        """The default importer config from AppConfig uses this pattern."""
        exprs = [
            ".*",
            "!https?://.*wsj\\.com/.*",
            "!https?://.*forbes\\.com/.*",
            "!https?://.*nytimes\\.com/.*",
        ]
        assert evaluate_importer_match(exprs, "https://example.com") is True
        assert evaluate_importer_match(exprs, "https://www.wsj.com/article") is False
        assert evaluate_importer_match(exprs, "https://forbes.com/story") is False
        assert evaluate_importer_match(exprs, "https://nytimes.com/2024/story") is False
        assert evaluate_importer_match(exprs, "https://bbc.com/news") is True

    def test_invalid_regex_raises(self):
        with pytest.raises(re.error):
            evaluate_importer_match(["[invalid"], "test")


# ---------------------------------------------------------------------------
# extract_drive_file_id
# ---------------------------------------------------------------------------


class TestExtractDriveFileId:
    def test_standard_drive_url(self):
        url = "https://drive.google.com/file/d/1ABC123xyz/view"
        assert extract_drive_file_id(url) == "1ABC123xyz"

    def test_open_url_with_id_param(self):
        url = "https://drive.google.com/open?id=1ABC123xyz"
        assert extract_drive_file_id(url) == "1ABC123xyz"

    def test_file_d_pattern(self):
        url = "https://drive.google.com/file/d/FILEID123/edit"
        assert extract_drive_file_id(url) == "FILEID123"

    def test_url_with_query_params_after_id(self):
        url = "https://drive.google.com/file/d/ABC123/view?usp=sharing"
        # The regex matches /d/([^/]+), so it captures up to the next /
        result = extract_drive_file_id(url)
        assert result == "ABC123"

    def test_non_drive_url(self):
        assert extract_drive_file_id("https://example.com/page") is None

    def test_empty_string(self):
        assert extract_drive_file_id("") is None

    def test_id_with_special_chars(self):
        url = "https://drive.google.com/file/d/1aB-cD_eF/view"
        assert extract_drive_file_id(url) == "1aB-cD_eF"


# ---------------------------------------------------------------------------
# normalize_source
# ---------------------------------------------------------------------------


class TestNormalizeSource:
    def test_http_passthrough(self):
        assert normalize_source("http://example.com") == "http://example.com"

    def test_https_passthrough(self):
        assert normalize_source("https://example.com") == "https://example.com"

    def test_gdrive_passthrough(self):
        assert normalize_source("gdrive:abc123") == "gdrive:abc123"

    def test_file_uri_resolved(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = normalize_source(f"file://{f}")
        assert result.startswith("file://")
        assert "test.txt" in result

    def test_existing_file_resolved(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_text("content")
        result = normalize_source(str(f))
        assert result.startswith("file://")
        assert "doc.pdf" in result

    def test_nonexistent_path_resolved(self, tmp_path):
        # Non-existent path still gets resolved to file:// URI
        result = normalize_source(str(tmp_path / "nonexistent.txt"))
        assert result.startswith("file://")


def test_import_source_uses_the_supplied_client() -> None:
    async def run_test() -> None:
        client = MagicMock()
        importer = MagicMock()
        with patch(
            "podcaster.research.execute_importer",
            new_callable=AsyncMock,
            return_value={"source_id": "source-1"},
        ) as execute_importer:
            result = await import_source(
                notebook_id="notebook-1",
                source="https://example.com/article",
                importer=importer,
                client=client,
            )

        assert result == {"source_id": "source-1"}
        execute_importer.assert_awaited_once_with(
            importer,
            client=client,
            notebook_id="notebook-1",
            source="https://example.com/article",
            title=None,
        )

    asyncio.run(run_test())


# ---------------------------------------------------------------------------
# poll_research_jobs fallback_importer and max_import_failures
# ---------------------------------------------------------------------------


class TestResearchFallbackAndImportFailures:
    def test_fallback_importer_uses_typed_research_source_url(self, monkeypatch):
        from podcaster.research import ResearchTask, poll_research_jobs

        source_url = "https://digitalcommons.uri.edu/cgi/viewcontent.cgi?article=4046"
        source = ResearchSource(url=source_url, title="Dolphin vocalizations")
        imported_sources = []

        class DummyResearchClient:
            async def poll(self, notebook_id):
                return {"status": "completed", "sources": [source]}

            async def import_sources(self, notebook_id, task_id, sources):
                raise RuntimeError("Native import failed")

        class DummyClientCtx:
            async def __aenter__(self):
                return type(
                    "DummyClient",
                    (),
                    {
                        "research": DummyResearchClient(),
                        "sources": type(
                            "DummySources",
                            (),
                            {
                                "list": lambda self, n: [],
                                "delete": lambda self, n, i: None,
                            },
                        )(),
                    },
                )()

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        async def fallback_import(notebook_id, url, importer, client, title=None):
            imported_sources.append((url, title))
            return {"source_id": "fallback-source"}

        monkeypatch.setattr(
            "podcaster.research.get_notebooklm_client", lambda _config: DummyClientCtx()
        )
        monkeypatch.setattr("podcaster.research.import_source", fallback_import)

        async def gen():
            yield ResearchTask(
                notebook_id="nb1",
                source_id="src1",
                task_id="t1",
                topic="Topic",
                summary="Summary",
                suggested_duration="default",
            )

        async def run_test():
            results = [
                result
                async for result in poll_research_jobs(
                    gen(),
                    NotebookLMConfig(),
                    fallback_importer=MagicMock(),
                )
            ]
            assert results[0].imported_count == 1

        asyncio.run(run_test())
        assert imported_sources == [(source_url, "Dolphin vocalizations")]

    def test_max_import_failures_exceeded_raises(self, monkeypatch):
        from podcaster.research import ResearchTask, poll_research_jobs

        # Mock client
        class DummyResearchClient:
            async def poll(self, notebook_id):
                return {
                    "status": "completed",
                    "sources": [
                        ResearchSource(url="https://bad1.com", title="Bad 1"),
                        ResearchSource(url="https://bad2.com", title="Bad 2"),
                    ],
                }

            async def import_sources(self, notebook_id, task_id, sources):
                raise RuntimeError("Native import failed")

        class DummyClientCtx:
            async def __aenter__(self):
                return type(
                    "DummyClient",
                    (),
                    {
                        "research": DummyResearchClient(),
                        "sources": type(
                            "DummySources",
                            (),
                            {
                                "list": lambda self, n: [],
                                "delete": lambda self, n, i: None,
                            },
                        )(),
                    },
                )()

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        monkeypatch.setattr(
            "podcaster.research.get_notebooklm_client", lambda _config: DummyClientCtx()
        )

        async def gen():
            yield ResearchTask(
                notebook_id="nb1",
                source_id="src1",
                task_id="t1",
                topic="Topic",
                summary="Summary",
                suggested_duration="default",
            )

        async def run_test():
            with pytest.raises(RuntimeError) as excinfo:
                async for _ in poll_research_jobs(
                    gen(), NotebookLMConfig(), fallback_importer=None, max_import_failures=1
                ):
                    pass

            assert "exceeded max_import_failures limit (1)" in str(excinfo.value)

        asyncio.run(run_test())

    def test_max_import_failures_none_tolerates_failures(self, monkeypatch):
        from podcaster.research import ResearchTask, poll_research_jobs

        class DummyResearchClient:
            async def poll(self, notebook_id):
                return {
                    "status": "completed",
                    "sources": [
                        ResearchSource(url="https://bad1.com", title="Bad 1")
                    ],
                }

            async def import_sources(self, notebook_id, task_id, sources):
                raise RuntimeError("Native import failed")

        class DummyClientCtx:
            async def __aenter__(self):
                return type(
                    "DummyClient",
                    (),
                    {
                        "research": DummyResearchClient(),
                        "sources": type(
                            "DummySources",
                            (),
                            {
                                "list": lambda self, n: [],
                                "delete": lambda self, n, i: None,
                            },
                        )(),
                    },
                )()

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        monkeypatch.setattr(
            "podcaster.research.get_notebooklm_client", lambda _config: DummyClientCtx()
        )

        async def gen():
            yield ResearchTask(
                notebook_id="nb1",
                source_id="src1",
                task_id="t1",
                topic="Topic",
                summary="Summary",
                suggested_duration="default",
            )

        async def run_test():
            results = []
            async for r in poll_research_jobs(
                gen(), NotebookLMConfig(), fallback_importer=None, max_import_failures=None
            ):
                results.append(r)

            assert len(results) == 1
            assert results[0].imported_count == 0

        asyncio.run(run_test())


# ---------------------------------------------------------------------------
# strip_citations
# ---------------------------------------------------------------------------


class TestStripCitations:
    def test_inline_single_citation(self):
        text = "This is a summary [1]."
        assert strip_citations(text) == "This is a summary."

    def test_inline_multiple_citations(self):
        text = "This is a summary [1, 2] with events [3]."
        assert strip_citations(text) == "This is a summary with events."

    def test_inline_range_citation(self):
        text = "Summary [1-3] text."
        assert strip_citations(text) == "Summary text."

    def test_inline_consecutive_citations(self):
        text = "Summary [1][2]."
        assert strip_citations(text) == "Summary."

    def test_trailing_sources_block(self):
        text = "Summary text.\n\nSources:\n[1] Source link"
        assert strip_citations(text) == "Summary text."

    def test_trailing_bracket_block(self):
        text = "Summary text.\n\n[1] https://example.com"
        assert strip_citations(text) == "Summary text."

    def test_no_citations(self):
        text = "Plain text summary."
        assert strip_citations(text) == "Plain text summary."


# ---------------------------------------------------------------------------
# parse_summary_response
# ---------------------------------------------------------------------------


class TestParseSummaryResponse:
    def test_clean_json(self):
        resp = '{"summary": "A clean summary.", "suggested_duration": "20 minutes"}'
        summary, duration = parse_summary_response(resp)
        assert summary == "A clean summary."
        assert duration == "20 minutes"

    def test_json_with_inline_citations(self):
        resp = '{"summary": "Summary with [1, 2] citations.", "suggested_duration": "15 minutes"}'
        summary, duration = parse_summary_response(resp)
        assert summary == "Summary with citations."
        assert duration == "15 minutes"

    def test_json_with_trailing_citations_and_code_fence(self):
        resp = (
            "```json\n"
            '{"summary": "Summary text [1].", "suggested_duration": "25 minutes"}\n'
            "```\n"
            "[1] Source link {details}"
        )
        summary, duration = parse_summary_response(resp)
        assert summary == "Summary text."
        assert duration == "25 minutes"

    def test_invalid_json_with_citation_in_structure(self):
        resp = '{"summary": "Summary text" [1], "suggested_duration": "30 minutes"}'
        summary, duration = parse_summary_response(resp)
        assert summary == "Summary text"
        assert duration == "30 minutes"

    def test_plain_text_fallback(self):
        resp = "This is a plain summary [1].\n\n[1] Reference"
        summary, duration = parse_summary_response(resp)
        assert summary == "This is a plain summary."
        assert duration == ""
