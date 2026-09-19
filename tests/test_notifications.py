"""Tests for codified workflow notifications and discord message formatting."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from podcaster.notifier.discord import (
    DEFAULT_DISCORD_TEMPLATE,
    send_discord_notification,
)
from podcaster.workflows import discover_workflow_plugins
from podcaster.workflows.deep_dive_article import (
    DeepDiveArticleNotification,
)
from podcaster.workflows.notifications import (
    ArtifactDistributionMetadata,
    NotebookDistributionMetadata,
    WorkflowDistributionMetadata,
)
from podcaster.workflows.topic_workflow import (
    TopicWorkflowNotification,
)


def test_deep_dive_article_notification_dict():
    notif = DeepDiveArticleNotification(
        preset="daily",
        title="AI Breakthroughs",
        notebook_url="https://notebooklm.google.com/notebook/123",
        source_url="https://example.com/article",
        languages=["en", "cs"],
        episodes=["Episode 1: State of AI", "Episode 2: Deep Dive"],
    )

    data = notif.to_notification_dict()
    assert data == {
        "workflow": "Deep Dive Article",
        "preset": "daily",
        "title": "AI Breakthroughs",
        "source": "https://example.com/article",
        "notebook": "https://notebooklm.google.com/notebook/123",
        "languages": ["en", "cs"],
        "episodes": ["Episode 1: State of AI", "Episode 2: Deep Dive"],
    }


def test_topic_workflow_notification_dict():
    notif = TopicWorkflowNotification(
        preset="topic-default",
        title="State of AI 2026",
        topic="AI Safety and Regulation",
        notebook_url="https://notebooklm.google.com/notebook/456",
        languages=["en"],
        podcasts=[
            "1. Deep dive on AI milestones",
            "2. US policymaker debate",
            "3. EU policymaker debate",
        ],
    )

    data = notif.to_notification_dict()
    assert data == {
        "workflow": "Topic Podcast",
        "preset": "topic-default",
        "title": "State of AI 2026",
        "topic": "AI Safety and Regulation",
        "notebook": "https://notebooklm.google.com/notebook/456",
        "languages": ["en"],
        "podcasts": [
            "1. Deep dive on AI milestones",
            "2. US policymaker debate",
            "3. EU policymaker debate",
        ],
    }


def test_workflow_distribution_metadata_model():
    wf_meta = WorkflowDistributionMetadata(
        id="wf_123",
        preset="topic-default",
        notebook=NotebookDistributionMetadata(
            id="nb_123",
            title="State of AI",
            url="https://notebooklm.google.com/notebook/nb_123",
            creation_date="2026-09-18",
        ),
        artifacts=[
            ArtifactDistributionMetadata(
                id="art_1",
                name="Deep Dive",
                language="en",
                path="/tmp/art_1.m4a",
                lrc_path="/tmp/art_1.lrc",
            )
        ],
        notification={
            "workflow": "Topic Podcast",
            "preset": "topic-default",
            "title": "State of AI",
            "topic": "AI Safety",
            "notebook": "https://notebooklm.google.com/notebook/nb_123",
            "languages": ["en"],
            "podcasts": ["1. Deep Dive"],
        },
    )

    dumped = wf_meta.model_dump()
    assert dumped["id"] == "wf_123"
    assert dumped["notebook"]["id"] == "nb_123"
    assert dumped["artifacts"][0]["name"] == "Deep Dive"
    assert dumped["notification"]["workflow"] == "Topic Podcast"


def test_discord_template_rendering():
    notif_dict = {
        "workflow": "Topic Podcast",
        "preset": "topic-default",
        "title": "State of AI 2026",
        "topic": "State of AI development 2026 September",
        "notebook": "https://notebooklm.google.com/notebook/123",
        "languages": ["en", "cs"],
        "podcasts": [
            "1. Deep dive on AI milestones",
            "2. US policymaker debate",
        ],
    }

    rendered = DEFAULT_DISCORD_TEMPLATE.render(
        metadata=notif_dict,
        dest_display="kolobok",
    ).strip()

    expected = (
        "🎙️ **Podcast Distributed**\n"
        "- **Destination**: `kolobok`\n"
        "- **Workflow**: Topic Podcast\n"
        "- **Preset**: topic-default\n"
        "- **Title**: State of AI 2026\n"
        "- **Topic**: State of AI development 2026 September\n"
        "- **Notebook**: https://notebooklm.google.com/notebook/123\n"
        "- **Languages**: en, cs\n"
        "- **Podcasts**:\n"
        "  • 1. Deep dive on AI milestones\n"
        "  • 2. US policymaker debate"
    )
    assert rendered == expected


@pytest.mark.anyio
async def test_send_discord_notification_extracts_nested_notification():
    metadata = {
        "id": "wf_123",
        "preset": "topic-default",
        "notebook": {"id": "nb_123", "title": "Test"},
        "notification": {
            "workflow": "Topic Podcast",
            "title": "State of AI",
        },
    }

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        res = await send_discord_notification(
            metadata=metadata,
            webhook_url="https://discord.com/api/webhooks/test",
            dist_result={"destination": "kolobok"},
        )

    assert res["status"] == "success"
    mock_client.post.assert_called_once()
    payload = mock_client.post.call_args[1]["json"]["content"]
    assert "🎙️ **Podcast Distributed**" in payload
    assert "- **Destination**: `kolobok`" in payload
    assert "- **Workflow**: Topic Podcast" in payload
    assert "- **Title**: State of AI" in payload
    # Raw non-notification keys should NOT be in the message
    assert "nb_123" not in payload


def test_plugins_declare_notification_type():
    plugins = discover_workflow_plugins()
    assert "deep_dive_article" in plugins
    assert plugins["deep_dive_article"].notification_type is DeepDiveArticleNotification
    assert "topic_workflow" in plugins
    assert plugins["topic_workflow"].notification_type is TopicWorkflowNotification


def test_cli_distribute_loads_metadata_json(tmp_path: Path):
    import json
    from types import SimpleNamespace

    from click.testing import CliRunner

    from podcaster.cli import cli

    meta_file = tmp_path / "metadata.json"
    meta_payload = {
        "id": "wf_test",
        "preset": "test_preset",
        "notification": {
            "workflow": "Topic Podcast",
            "title": "Test Title",
        },
    }
    meta_file.write_text(json.dumps(meta_payload), encoding="utf-8")

    captured_metadata = []

    class DummyDistribution:
        async def distribute(self, working_dir, metadata=None):
            captured_metadata.append(metadata)
            return {"status": "success"}

    with (
        patch("podcaster.cli.distribute.load_config") as mock_load_config,
        patch(
            "podcaster.distribution.build_distribution",
            return_value=DummyDistribution(),
        ),
    ):
        mock_config = SimpleNamespace(distributions={"test_preset": object()})
        mock_load_config.return_value = mock_config

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["distribute", "--workdir", str(tmp_path), "--preset", "test_preset"],
        )
        assert result.exit_code == 0, f"Failed with {result.output}"
        assert len(captured_metadata) == 1
        assert captured_metadata[0] == meta_payload
