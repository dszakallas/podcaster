"""Tests for agentic scraper process management and timeout handling."""

import sys
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from podcaster.cli import cli
from podcaster.config import AgentConfig, ScraperConfig
from podcaster.research import (
    ScraperImporter,
    scrape_source,
)
from podcaster.utils.process import ProcessTimeoutError


@pytest.mark.anyio
async def test_scrape_source_normal_success():
    """Verify normal execution without timeout parses NDJSON correctly."""
    result = await scrape_source(
        "https://example.com",
        command=sys.executable,
        args=["-c", 'print(\'{"title": "Test Title", "content": "Test content"}\')'],
    )
    assert result is not None
    assert result["title"] == "Test Title"
    assert result["content"] == "Test content"


@pytest.mark.anyio
async def test_scrape_source_timeout_sigterm_graceful_shutdown():
    """Verify that when a process exceeds timeout, SIGTERM terminates it."""
    script = "import time, sys; sys.stdout.write('STARTED\\n'); sys.stdout.flush(); time.sleep(30)"

    with pytest.raises(ProcessTimeoutError, match="timed out after 0.2 seconds"):
        await scrape_source(
            "https://example.com",
            command=sys.executable,
            args=["-c", script],
            timeout=0.2,
            grace_period=0.5,
        )


@pytest.mark.anyio
async def test_scrape_source_timeout_two_way_fallback_to_sigkill():
    """Verify that when a process ignores SIGTERM, SIGKILL terminates it."""
    # Script ignores SIGTERM
    script = (
        "import signal, time, sys; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "sys.stdout.write('READY\\n'); "
        "sys.stdout.flush(); "
        "time.sleep(30)"
    )

    with pytest.raises(ProcessTimeoutError, match="timed out after 0.2 seconds"):
        await scrape_source(
            "https://example.com",
            command=sys.executable,
            args=["-c", script],
            timeout=0.2,
            grace_period=0.2,
        )


@pytest.mark.anyio
async def test_scrape_source_timeout_from_scraper_config():
    """Verify timeout configured on ScraperConfig is respected."""
    scraper_cfg = ScraperConfig(
        tool="playwright",
        agent=AgentConfig(
            command=sys.executable,
            args=["-c", "import time; time.sleep(30)"],
        ),
        timeout=0.2,
    )
    with pytest.raises(ProcessTimeoutError, match="timed out after 0.2 seconds"):
        await scrape_source(
            "https://example.com",
            scraper_config=scraper_cfg,
            grace_period=0.2,
        )


@pytest.mark.anyio
async def test_scrape_source_timeout_from_agent_config():
    """Verify timeout configured on AgentConfig is respected when not on ScraperConfig."""
    scraper_cfg = ScraperConfig(
        tool="playwright",
        agent=AgentConfig(
            command=sys.executable,
            args=["-c", "import time; time.sleep(30)"],
            timeout=0.2,
        ),
    )
    with pytest.raises(ProcessTimeoutError, match="timed out after 0.2 seconds"):
        await scrape_source(
            "https://example.com",
            scraper_config=scraper_cfg,
            grace_period=0.2,
        )


@pytest.mark.anyio
async def test_scrape_source_explicit_timeout_overrides_config():
    """Verify explicit timeout parameter overrides ScraperConfig timeout."""
    scraper_cfg = ScraperConfig(
        tool="playwright",
        agent=AgentConfig(
            command=sys.executable,
            args=["-c", "import time; time.sleep(30)"],
        ),
        timeout=60.0,
    )
    with pytest.raises(ProcessTimeoutError, match="timed out after 0.2 seconds"):
        await scrape_source(
            "https://example.com",
            scraper_config=scraper_cfg,
            timeout=0.2,
            grace_period=0.2,
        )


@pytest.mark.anyio
async def test_scrape_source_rejects_negative_or_zero_timeout():
    """Verify non-positive timeout raises ValueError."""
    with pytest.raises(ValueError, match="Timeout must be positive"):
        await scrape_source(
            "https://example.com",
            command=sys.executable,
            args=["-c", "pass"],
            timeout=0,
        )

    with pytest.raises(ValueError, match="Timeout must be positive"):
        await scrape_source(
            "https://example.com",
            command=sys.executable,
            args=["-c", "pass"],
            timeout=-5,
        )


@pytest.mark.anyio
async def test_scraper_importer_handles_timeout_error():
    """Verify ScraperImporter returns error dictionary when timeout occurs."""
    scraper_cfg = ScraperConfig(
        tool="playwright",
        agent=AgentConfig(
            command=sys.executable,
            args=["-c", "import time; time.sleep(30)"],
        ),
        timeout=0.2,
    )
    importer = ScraperImporter(config=scraper_cfg, grace_period=0.2)
    client_mock = MagicMock()

    result = await importer.execute(
        notebook_id="test-nb",
        source="https://example.com/article",
        client=client_mock,
    )
    assert result["source_id"] is None
    assert "[scraper]:" in result["error"]
    assert "timed out after 0.2 seconds" in result["error"]


def test_cli_scrape_rejects_invalid_timeout():
    """Verify CLI scrape command rejects non-positive timeout values."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["scrape", "https://example.com", "--scraper", "default", "--timeout", "0"]
    )
    assert result.exit_code != 0
    assert "Timeout must be positive" in result.output
