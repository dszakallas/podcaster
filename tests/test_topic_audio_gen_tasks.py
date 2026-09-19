"""Unit tests for topic deep dive and topic debate audio generation tasks."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from notebooklm import AudioFormat

from podcaster.audio_gen.params import AudioGenParams
from podcaster.audio_gen.tasks.gen_podcast.main_article_with_author import (
    main_article_with_author,
)
from podcaster.audio_gen.tasks.gen_podcast.topic_debate import (
    topic_debate,
)
from podcaster.audio_gen.tasks.gen_podcast.topic_deep_dive import (
    topic_deep_dive,
)


def test_audio_format_constants():
    assert topic_deep_dive.AUDIO_FORMAT == AudioFormat.DEEP_DIVE
    assert topic_debate.AUDIO_FORMAT == AudioFormat.DEBATE


def test_topic_deep_dive_prompt_with_explicit_roles_and_focus():
    async def _test():
        mock_client = MagicMock()
        mock_client.chat.ask = AsyncMock()

        inputs = topic_deep_dive.Inputs(
            topic="Artificial General Intelligence",
            focus="Economic impact on labor markets",
            roles=["Senior Tech Journalist", "Labor Economist"],
            agenda="- Trend analysis\n- Policy recommendations",
        )
        params = AudioGenParams(notebook_id="nb-123", length="25 minutes")

        prompt = await topic_deep_dive.get_prompt(mock_client, inputs, params)

        assert "Senior Tech Journalist" in prompt
        assert "Labor Economist" in prompt
        assert "Artificial General Intelligence" in prompt
        assert "Economic impact on labor markets" in prompt
        assert "Trend analysis" in prompt
        assert "25 minutes" in prompt
        # When all roles and agenda are explicit, chat inference is not needed
        mock_client.chat.ask.assert_not_called()

    asyncio.run(_test())


def test_topic_deep_dive_prompt_infers_missing_roles():
    async def _test():
        mock_client = MagicMock()
        mock_client.chat.ask = AsyncMock(
            return_value=MagicMock(
                answer='{"category": "Technology", "roles": ["Investigative Host", "AI Specialist"], "agenda": "- Intro\\n- Deep dive"}'
            )
        )

        inputs = topic_deep_dive.Inputs(
            topic="Renewable Energy Storage",
            focus="Battery chemistry",
        )
        params = AudioGenParams(notebook_id="nb-123", length="15 minutes")

        prompt = await topic_deep_dive.get_prompt(mock_client, inputs, params)

        mock_client.chat.ask.assert_called_once()
        assert "Investigative Host" in prompt
        assert "AI Specialist" in prompt
        assert "Battery chemistry" in prompt

    asyncio.run(_test())


def test_topic_debate_prompt_with_explicit_stances_and_focus():
    async def _test():
        mock_client = MagicMock()
        mock_client.chat.ask = AsyncMock(return_value=MagicMock(answer="{}"))

        inputs = topic_debate.Inputs(
            topic="Nuclear Power Expansion",
            focus="Small Modular Reactors",
            roles=["Nuclear Energy Proponent", "Renewable Grid Analyst"],
        )
        params = AudioGenParams(notebook_id="nb-456", length="30 minutes")

        prompt = await topic_debate.get_prompt(mock_client, inputs, params)

        assert "Nuclear Energy Proponent" in prompt
        assert "Renewable Grid Analyst" in prompt
        assert "Small Modular Reactors" in prompt
        assert "30 minutes" in prompt

    asyncio.run(_test())


def test_topic_debate_prompt_infers_missing_stances():
    async def _test():
        mock_client = MagicMock()
        mock_client.chat.ask = AsyncMock(
            return_value=MagicMock(
                answer='{"category": "Ethics", "roles": ["Techno-Optimist", "Safety Researcher"], "agenda": "- Core clash\\n- Tradeoffs"}'
            )
        )

        inputs = topic_debate.Inputs(
            topic="Autonomous Weapons",
        )
        params = AudioGenParams(notebook_id="nb-456", length="20 minutes")

        prompt = await topic_debate.get_prompt(mock_client, inputs, params)

        mock_client.chat.ask.assert_called_once()
        assert "Techno-Optimist" in prompt
        assert "Safety Researcher" in prompt
        assert "Autonomous Weapons" in prompt

    asyncio.run(_test())


def test_main_article_with_author_explicit_roles_and_focus():
    async def _test():
        mock_client = MagicMock()
        mock_client.chat.ask = AsyncMock()
        mock_client.sources.get = AsyncMock(
            return_value=MagicMock(id="src-1", title="Neural Scaling Laws")
        )
        mock_client.sources.get_guide = AsyncMock(
            return_value=MagicMock(summary="Comprehensive paper on compute scaling.")
        )

        inputs = main_article_with_author.Inputs(
            source_id="src-1",
            topic="AI Scaling",
            focus="Compute thresholds",
            roles=["Tech Journalist", "Lead Researcher"],
            agenda="- Scaling limits\n- Efficiency",
        )
        params = AudioGenParams(notebook_id="nb-789", length="20 minutes")

        prompt = await main_article_with_author.get_prompt(mock_client, inputs, params)

        assert "Tech Journalist" in prompt
        assert "Lead Researcher" in prompt
        assert "Neural Scaling Laws" in prompt
        assert "Compute thresholds" in prompt
        assert "Scaling limits" in prompt
        assert "20 minutes" in prompt
        # Explicit roles and agenda -> chat inference skipped
        mock_client.chat.ask.assert_not_called()

    asyncio.run(_test())


def test_main_article_with_author_infers_missing_roles():
    async def _test():
        mock_client = MagicMock()
        mock_client.sources.get = AsyncMock(
            return_value=MagicMock(id="src-1", title="Cybersecurity in Cloud Native")
        )
        mock_client.sources.get_guide = AsyncMock(
            return_value=MagicMock(summary="Analysis of zero-trust security postures.")
        )
        mock_client.chat.ask = AsyncMock(
            return_value=MagicMock(
                answer='{"category": "Security", "roles": ["Investigative Host", "Security Architect"], "agenda": "- Attack vectors"}'
            )
        )

        inputs = main_article_with_author.Inputs(
            source_id="src-1",
            focus="Zero-trust enforcement",
        )
        params = AudioGenParams(notebook_id="nb-789", length="15 minutes")

        prompt = await main_article_with_author.get_prompt(mock_client, inputs, params)

        mock_client.chat.ask.assert_called_once()
        assert "Investigative Host" in prompt
        assert "Security Architect" in prompt
        assert "Zero-trust enforcement" in prompt

    asyncio.run(_test())


def test_main_article_with_author_resolves_source_by_summary_match():
    async def _test():
        mock_client = MagicMock()
        src_general = MagicMock(id="src-gen", title="General AI Overview")
        src_safety = MagicMock(id="src-safe", title="Benchmarking LLM Vulnerabilities")

        mock_client.sources.list = AsyncMock(return_value=[src_general, src_safety])

        async def fake_get_guide(nb_id, s_id):
            if s_id == "src-safe":
                return MagicMock(
                    summary="Detailed investigation into evaluation benchmark vulnerabilities and testing loopholes."
                )
            return MagicMock(
                summary="High-level overview of foundation models and consumer products."
            )

        mock_client.sources.get_guide = AsyncMock(side_effect=fake_get_guide)

        async def fake_get(nb_id, s_id):
            if s_id == "src-safe":
                return src_safety
            return src_general

        mock_client.sources.get = AsyncMock(side_effect=fake_get)
        mock_client.chat.ask = AsyncMock(
            return_value=MagicMock(
                answer='{"source_id": "src-safe", "category": "Tech", "roles": ["Interviewer", "Auditor"], "agenda": "- Loopholes"}'
            )
        )

        # source_id is omitted; topic & focus specified
        inputs = main_article_with_author.Inputs(
            topic="AI Safety",
            focus="evaluation benchmark vulnerabilities",
            roles=["Interviewer", "Auditor"],
            agenda="- Loopholes",
        )
        params = AudioGenParams(notebook_id="nb-789", length="25 minutes")

        prompt = await main_article_with_author.get_prompt(mock_client, inputs, params)

        mock_client.sources.get.assert_called_with("nb-789", "src-safe")
        assert "Benchmarking LLM Vulnerabilities" in prompt
        assert "Auditor" in prompt

    asyncio.run(_test())
