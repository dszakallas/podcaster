"""Unit tests for pure utility functions in podcaster.research."""

import asyncio
import re

import pytest

from podcaster.research import (
    evaluate_importer_match,
    extract_drive_file_id,
    normalize_source,
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


# ---------------------------------------------------------------------------
# poll_research_jobs fallback_importer and max_import_failures
# ---------------------------------------------------------------------------


class TestResearchFallbackAndImportFailures:
    def test_max_import_failures_exceeded_raises(self, monkeypatch):
        from podcaster.research import ResearchTask, poll_research_jobs

        # Mock client
        class DummyResearchClient:
            async def poll(self, notebook_id):
                return {
                    "status": "completed",
                    "sources": [
                        {"url": "https://bad1.com"},
                        {"url": "https://bad2.com"},
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
            "podcaster.research.get_notebooklm_client", lambda: DummyClientCtx()
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
                    gen(), fallback_importer=None, max_import_failures=1
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
                    "sources": [{"url": "https://bad1.com"}],
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
            "podcaster.research.get_notebooklm_client", lambda: DummyClientCtx()
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
                gen(), fallback_importer=None, max_import_failures=None
            ):
                results.append(r)

            assert len(results) == 1
            assert results[0].imported_count == 0

        asyncio.run(run_test())
