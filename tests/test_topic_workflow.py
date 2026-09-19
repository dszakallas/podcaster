"""Unit tests for topic workflow execution and step orchestration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from podcaster.config import (
    EnrichWebConfig,
    GenerateCoverConfig,
    NotebookLMConfig,
    PodcastGenerationConfig,
    PodcastTagsConfig,
    PodcastTranscriptionConfig,
    TaggingConfig,
    TranscribeConfig,
)
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
    ResearchResult,
    TaskStatus,
)
from podcaster.workflows.common import WorkflowEnvironment
from podcaster.workflows.topic_workflow.config import TopicWorkflowConfig
from podcaster.workflows.topic_workflow.models import (
    ResearchDescriptor,
    TopicArticleDescriptor,
    TopicDebateDescriptor,
    TopicDeepDiveDescriptor,
    TopicWorkflowRecipe,
)
from podcaster.workflows.topic_workflow.workflow import (
    enrich_topic_query_step,
    init_topic_notebook_step,
    topic_workflow,
)


def _sample_recipe() -> TopicWorkflowRecipe:
    return TopicWorkflowRecipe(
        title="AI Frontiers",
        research=ResearchDescriptor(query="Frontier AI reasoning models", mode="deep"),
        podcasts=[
            TopicDeepDiveDescriptor(
                type="TopicDeepDive",
                focus="Cognitive labor impact",
                length="default",
                languages=["en"],
            ),
            TopicDebateDescriptor(
                type="TopicDebate",
                focus="Open weights security tradeoffs",
                roles=["Security Researcher", "Open Source Proponent"],
                length="long",
                languages=["en"],
            ),
            TopicArticleDescriptor(
                type="TopicArticle",
                focus="Safety benchmarks",
                roles=["AI Ethicist", "Lead Author"],
                agenda="Discuss paper findings",
                length="short",
                languages=["en"],
            ),
        ],
    )


def _sample_config() -> TopicWorkflowConfig:
    return TopicWorkflowConfig(
        type="topic_workflow",
        podcast_generator=PodcastGenerationConfig(languages=["en"], length="default"),
        enrich_web=EnrichWebConfig(enable=True),
        generate_cover=GenerateCoverConfig(enable=True),
        transcribe=TranscribeConfig(
            enable=False,
            podcast_transcriber=PodcastTranscriptionConfig(languages=["en"]),
        ),
        tagging=TaggingConfig(enable=False, spec=PodcastTagsConfig()),
        distribute=[],
    )


def test_init_topic_notebook_step_calls_module(dbos_session):
    async def _test():
        with patch(
            "podcaster.workflows.topic_workflow.workflow.notebook_mod.init_topic_notebook",
            new_callable=AsyncMock,
        ) as mock_init:
            mock_init.return_value = {
                "notebook_id": "nb-1",
                "derived_title": "AI Frontiers",
                "source_id": "src-1",
                "created_at": "2026-08-24T12:00:00Z",
            }
            res = await init_topic_notebook_step(
                notebooklm_config=NotebookLMConfig(),
                topic="AI Frontiers",
                title="AI Frontiers",
            )
            assert res["notebook_id"] == "nb-1"
            mock_init.assert_called_once()

    asyncio.run(_test())


def test_enrich_topic_query_step_calls_module(dbos_session):
    async def _test():
        with patch(
            "podcaster.workflows.topic_workflow.workflow.research.research_from_query",
            new_callable=AsyncMock,
        ) as mock_res:
            mock_res.return_value = ResearchResult(
                notebook_id="nb-1",
                source_id="src-1",
                task_id="t-1",
                topic="AI Frontiers",
                summary="Summary",
                suggested_duration="20 minutes",
                status=TaskStatus.COMPLETED,
            )
            res = await enrich_topic_query_step(
                notebook_id="nb-1",
                query="AI Frontiers",
                mode="deep",
                enrich_config=EnrichWebConfig(),
                notebooklm_config=NotebookLMConfig(),
            )
            assert res.topic == "AI Frontiers"
            mock_res.assert_called_once()

    asyncio.run(_test())


def test_topic_workflow_runs_multi_podcasts(dbos_session):
    async def _test():
        recipe = _sample_recipe()
        wf_config = _sample_config()

        mock_nb_info = {
            "notebook_id": "nb-test",
            "derived_title": "AI Frontiers",
            "source_id": "src-test",
            "created_at": "2026-08-24T12:00:00Z",
        }

        created_audio_tasks: list[dict] = []

        async def mock_create_audio_jobs(
            notebook_id, task_name, languages, length, format_args, **kwargs
        ):
            created_audio_tasks.append(
                {
                    "task_name": task_name,
                    "languages": languages,
                    "length": length,
                    "format_args": format_args,
                }
            )
            return [
                PodcastGenTask(
                    notebook_id=notebook_id,
                    task_id=f"task-{task_name}",
                    status=TaskStatus.COMPLETED,
                    metadata={"generate-podcast": {"language": "en"}},
                )
            ]

        async def mock_process_audio_tasks(audio_tasks, **kwargs):
            return [
                PodcastGenArtifact(
                    notebook_id=t.notebook_id,
                    artifact_id=t.task_id,
                    title=f"Episode {t.task_id}",
                    path=f"/tmp/{t.task_id}.m4a",
                    filename=f"{t.task_id}.m4a",
                )
                for t in audio_tasks
            ]

        with (
            patch(
                "podcaster.workflows.topic_workflow.workflow.init_topic_notebook_step",
                new_callable=AsyncMock,
                return_value=mock_nb_info,
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.generate_cover_step",
                new_callable=AsyncMock,
                return_value="/tmp/cover.jpg",
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.enrich_topic_query_step",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.create_topic_podcast_audio_jobs_step",
                side_effect=mock_create_audio_jobs,
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.process_audio_tasks",
                side_effect=mock_process_audio_tasks,
            ),
        ):
            env = WorkflowEnvironment(
                workdir="/tmp/podcaster_test",
                workflow_id="wf_topic_123",
                notebooklm_config=NotebookLMConfig(),
            )
            result = await topic_workflow(
                preset_name="test-topic-preset",
                wf_config=wf_config,
                recipe=recipe,
                env=env,
            )

            assert result["workflow_id"] == "wf_topic_123"
            assert result["notebook_id"] == "nb-test"
            assert len(result["files"]) == 3
            assert len(created_audio_tasks) == 3

            deep_dive_call = next(
                t for t in created_audio_tasks if t["task_name"] == "topic-deep-dive"
            )
            assert deep_dive_call["format_args"]["focus"] == "Cognitive labor impact"

            debate_call = next(
                t for t in created_audio_tasks if t["task_name"] == "topic-debate"
            )
            assert (
                debate_call["format_args"]["focus"] == "Open weights security tradeoffs"
            )
            assert debate_call["format_args"]["roles"] == [
                "Security Researcher",
                "Open Source Proponent",
            ]

            article_call = next(
                t
                for t in created_audio_tasks
                if t["task_name"] == "main-article-with-author"
            )
            assert article_call["format_args"]["focus"] == "Safety benchmarks"
            assert article_call["format_args"]["roles"] == [
                "AI Ethicist",
                "Lead Author",
            ]
            assert article_call["format_args"]["agenda"] == "Discuss paper findings"

    asyncio.run(_test())


def test_infer_recipe_from_prompt_parses_json():
    from podcaster.workflows.topic_workflow.cli import infer_recipe_from_prompt

    async def _test():
        raw_json_response = """```json
{
  "title": "State of AI 2026",
  "research": {
    "query": "State of AI 2026 breakthroughs policy",
    "mode": "deep"
  },
  "podcasts": [
    {
      "type": "TopicDeepDive",
      "focus": "Chronological summary 2024-2026"
    },
    {
      "type": "TopicDebate",
      "focus": "US vs EU AI Regulation",
      "roles": ["Pro-regulation advocate", "Pro-innovation strategist"],
      "languages": ["en", "fr"],
      "length": "long"
    }
  ]
}
```"""

        mock_chat_res = MagicMock()
        mock_chat_res.answer = raw_json_response

        mock_client = AsyncMock()
        mock_client.notebooks.create.return_value = MagicMock(id="scratch-nb-123")
        mock_client.sources.add_text.return_value = MagicMock(id="src-123")
        mock_client.chat.ask.return_value = mock_chat_res
        mock_client.notebooks.delete.return_value = None

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_client_cm(_):
            yield mock_client

        with patch(
            "podcaster.workflows.topic_workflow.cli.get_notebooklm_client",
            side_effect=fake_client_cm,
        ):
            recipe = await infer_recipe_from_prompt(
                "Test prompt",
                NotebookLMConfig(),
            )

        assert recipe.title == "State of AI 2026"
        assert recipe.research.mode == "deep"
        assert len(recipe.podcasts) == 2
        assert isinstance(recipe.podcasts[0], TopicDeepDiveDescriptor)
        assert recipe.podcasts[0].languages is None
        assert recipe.podcasts[0].length is None
        assert isinstance(recipe.podcasts[1], TopicDebateDescriptor)
        assert recipe.podcasts[1].languages == ["en", "fr"]
        assert recipe.podcasts[1].length == "long"
        assert recipe.podcasts[1].roles == [
            "Pro-regulation advocate",
            "Pro-innovation strategist",
        ]
        mock_client.notebooks.delete.assert_awaited_once_with("scratch-nb-123")

    asyncio.run(_test())


def test_topic_workflow_auto_length_uses_research_suggested_duration(dbos_session):
    async def _test():
        recipe = TopicWorkflowRecipe(
            title="Auto Length Test",
            research=ResearchDescriptor(query="Quantum computing", mode="fast"),
            podcasts=[
                TopicDeepDiveDescriptor(
                    type="TopicDeepDive",
                    focus="Hardware breakthroughs",
                    length="auto",
                )
            ],
        )
        wf_config = _sample_config()
        wf_config.podcast_generator = PodcastGenerationConfig(
            languages=["en"], length="auto"
        )

        mock_nb_info = {
            "notebook_id": "nb-auto",
            "derived_title": "Auto Length Test",
            "source_id": "src-seed",
            "created_at": "2026-08-24T12:00:00Z",
        }
        audio_job_calls = []

        async def mock_create_audio_jobs(
            notebook_id,
            task_name,
            languages,
            length,
            format_args,
            generator_config,
            notebooklm_config,
            **kwargs,
        ):
            audio_job_calls.append({"task_name": task_name, "length": length})
            return [
                PodcastGenTask(
                    notebook_id=notebook_id,
                    task_id="task-1",
                    eta=600.0,
                    generation_started_at=0.0,
                )
            ]

        with (
            patch(
                "podcaster.workflows.topic_workflow.workflow.init_topic_notebook_step",
                new_callable=AsyncMock,
                return_value=mock_nb_info,
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.generate_cover_step",
                new_callable=AsyncMock,
                return_value="/tmp/cover.jpg",
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.enrich_topic_query_step",
                new_callable=AsyncMock,
                return_value=MagicMock(suggested_duration="25 minutes"),
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.create_topic_podcast_audio_jobs_step",
                side_effect=mock_create_audio_jobs,
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.process_audio_tasks",
                new_callable=AsyncMock,
                return_value=[
                    PodcastGenArtifact(
                        notebook_id="nb-auto",
                        artifact_id="art-1",
                        title="Audio 1",
                        path="/tmp/audio.mp3",
                        filename="audio.mp3",
                    )
                ],
            ),
        ):
            env = WorkflowEnvironment(
                workdir="/tmp/podcaster_test",
                workflow_id="wf_auto_1",
                notebooklm_config=NotebookLMConfig(),
            )
            await topic_workflow(
                preset_name="test-topic-preset",
                wf_config=wf_config,
                recipe=recipe,
                env=env,
            )

            assert len(audio_job_calls) == 1
            assert audio_job_calls[0]["length"] == "25 minutes"

    asyncio.run(_test())


def test_topic_workflow_auto_length_uses_infer_step_when_no_research(dbos_session):
    async def _test():
        recipe = TopicWorkflowRecipe(
            title="Auto Length Inference",
            research=ResearchDescriptor(query="Quantum Computing", mode="fast"),
            podcasts=[TopicDeepDiveDescriptor(length="auto", focus="Hardware scaling")],
        )
        wf_config = _sample_config()
        wf_config.enrich_web.enable = False
        wf_config.podcast_generator = PodcastGenerationConfig(
            languages=["en"], length="auto"
        )

        audio_job_calls = []

        async def mock_create_jobs(**kwargs):
            audio_job_calls.append(kwargs)
            return [
                PodcastGenTask(
                    notebook_id="nb-step",
                    task_id="task-infer",
                    status=TaskStatus.COMPLETED,
                    metadata={"track": 1, "total_tracks": 1},
                )
            ]

        with (
            patch(
                "podcaster.workflows.topic_workflow.workflow.init_topic_notebook_step",
                new_callable=AsyncMock,
                return_value={
                    "notebook_id": "nb-step",
                    "derived_title": "Auto Length Inference",
                    "source_id": "src-step",
                },
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.generate_cover_step",
                new_callable=AsyncMock,
                return_value="/tmp/cover.jpg",
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.enrich_topic_query_step",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.infer_topic_podcast_duration_step",
                new_callable=AsyncMock,
                return_value="35 minutes",
            ) as mock_infer_step,
            patch(
                "podcaster.workflows.topic_workflow.workflow.create_topic_podcast_audio_jobs_step",
                side_effect=mock_create_jobs,
            ),
            patch(
                "podcaster.workflows.topic_workflow.workflow.process_audio_tasks",
                new_callable=AsyncMock,
                return_value=[
                    PodcastGenArtifact(
                        notebook_id="nb-step",
                        artifact_id="art-1",
                        title="Audio 1",
                        path="/tmp/audio.mp3",
                        filename="audio.mp3",
                    )
                ],
            ),
        ):
            env = WorkflowEnvironment(
                workdir="/tmp/podcaster_test",
                workflow_id="wf_auto_2",
                notebooklm_config=NotebookLMConfig(),
            )
            await topic_workflow(
                preset_name="test-topic-preset",
                wf_config=wf_config,
                recipe=recipe,
                env=env,
            )

            assert len(audio_job_calls) == 1
            assert audio_job_calls[0]["length"] == "35 minutes"
            mock_infer_step.assert_called_once()

    asyncio.run(_test())
