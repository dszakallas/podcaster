"""Tests for explicit configuration requirements."""

import pytest
from pydantic import ValidationError

from podcaster.config import (
    DEFAULT_COVER_MODEL,
    AppConfig,
    GenerateCoverConfig,
    GenerateCoverSpecConfig,
    ImporterConfig,
    PodcastGenerationConfig,
    ScraperConfig,
    TaggingConfig,
    resolve_refs,
)
from podcaster.workflows.deep_dive_article.config import DeepDiveArticleConfig


def test_app_config_requires_every_top_level_section():
    with pytest.raises(ValidationError) as exc_info:
        AppConfig.model_validate({})

    missing_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert missing_fields == {
        "dbos",
        "scrapers",
        "agents",
        "podcast_generators",
        "podcast_transcribers",
        "importers",
        "podcast_tags",
        "workflow",
        "notifiers",
        "distributions",
        "gcp",
    }


def test_scraper_requires_an_agent():
    with pytest.raises(ValidationError, match="agent"):
        ScraperConfig.model_validate({"tool": "playwright"})


def test_tagging_requires_a_specification():
    with pytest.raises(ValidationError, match="spec"):
        TaggingConfig.model_validate({})


def test_workflow_requires_every_step_configuration():
    with pytest.raises(ValidationError) as exc_info:
        DeepDiveArticleConfig.model_validate(
            {
                "type": "deep_dive_article",
                "podcast_generator": PodcastGenerationConfig(),
                "importer": ImporterConfig(),
            }
        )

    missing_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert missing_fields == {
        "enrich_web",
        "generate_cover",
        "transcribe",
        "tagging",
        "distribute",
    }


def _base_config() -> dict:
    return {
        "dbos": {},
        "scrapers": {},
        "agents": {},
        "podcast_generators": {},
        "podcast_transcribers": {},
        "importers": {},
        "podcast_tags": {},
        "workflow": {"presets": {}},
        "notifiers": {},
        "distributions": {},
        "gcp": {},
    }


def test_notifier_config_prefers_scoped_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_data = _base_config()
    config_data["notifiers"] = {"daily news": {"discord": {}}}
    monkeypatch.setenv("NOTIFIERS_DAILY_NEWS_DISCORD_WEBHOOK_URL", "scoped-webhook")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "global-webhook")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "global-token")

    config = resolve_refs(AppConfig.model_validate(config_data))
    discord = config.notifiers["daily news"].discord

    assert discord is not None
    assert discord.webhook_url == "scoped-webhook"
    assert discord.bot_token == "global-token"


def test_inline_notifier_config_uses_unscoped_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_data = _base_config()
    config_data["distributions"] = {
        "release": {
            "rsync": {"destination": "podcasts"},
            "notifiers": [{"discord": {}}],
        }
    }
    monkeypatch.setenv("DISTRIBUTIONS_RELEASE_DISCORD_WEBHOOK_URL", "scoped-webhook")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "global-webhook")

    config = resolve_refs(AppConfig.model_validate(config_data))
    notifier = config.distributions["release"].notifiers[0]

    assert notifier.discord is not None
    assert notifier.discord.webhook_url == "global-webhook"


def test_notebooklm_config_reads_optional_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_data = _base_config()
    monkeypatch.setenv("NOTEBOOKLM_HOME", "/tmp/notebooklm")
    monkeypatch.setenv("NOTEBOOKLM_STORAGE_STATE", "/tmp/storage_state.json")
    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "work")

    config = resolve_refs(AppConfig.model_validate(config_data))

    assert config.notebooklm.home == "/tmp/notebooklm"
    assert config.notebooklm.storage_state == "/tmp/storage_state.json"
    assert config.notebooklm.profile == "work"


def test_generate_cover_spec_defaults_to_default_cover_model():
    spec = GenerateCoverSpecConfig()
    assert spec.model == DEFAULT_COVER_MODEL


def test_generate_cover_spec_allows_custom_model():
    spec = GenerateCoverSpecConfig(model="imagen-3.0-generate-002")
    assert spec.model == "imagen-3.0-generate-002"


def test_generate_cover_spec_forbids_extra_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        GenerateCoverSpecConfig.model_validate({"extra_field": "invalid"})
