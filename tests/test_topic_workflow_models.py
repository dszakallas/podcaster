"""Unit tests for topic workflow models and recipe schema."""

import pytest
import yaml
from pydantic import ValidationError

from podcaster.workflows.topic_workflow.models import (
    TopicArticleDescriptor,
    TopicDebateDescriptor,
    TopicDeepDiveDescriptor,
    TopicWorkflowRecipe,
)


def test_recipe_validation_success():
    raw_yaml = """
title: "The Future of Quantum Computing"
research:
  query: "Quantum error correction and practical hardware breakthroughs"
  mode: "deep"
podcasts:
  - type: "TopicDeepDive"
    focus: "Neutral atom hardware advances"
    languages: ["en"]
    length: "default"
  - type: "TopicDebate"
    focus: "Superconducting qubits vs trapped ions: commercial scalability"
    roles:
      - "Superconducting Physicist"
      - "Ion Trap Researcher"
    length: "long"
  - type: "TopicArticle"
    focus: "Neutral Atom Breakthrough Paper"
    roles:
      - "Tech Journalist"
      - "Lead Author"
    source_id: "src-123"
"""
    data = yaml.safe_load(raw_yaml)
    recipe = TopicWorkflowRecipe.model_validate(data)

    assert recipe.title == "The Future of Quantum Computing"
    assert (
        recipe.research.query
        == "Quantum error correction and practical hardware breakthroughs"
    )
    assert recipe.research.mode == "deep"
    assert len(recipe.podcasts) == 3

    deep_dive = recipe.podcasts[0]
    assert isinstance(deep_dive, TopicDeepDiveDescriptor)
    assert deep_dive.type == "TopicDeepDive"
    assert deep_dive.focus == "Neutral atom hardware advances"
    assert deep_dive.roles is None
    assert deep_dive.languages == ["en"]
    assert deep_dive.length == "default"

    debate = recipe.podcasts[1]
    assert isinstance(debate, TopicDebateDescriptor)
    assert debate.type == "TopicDebate"
    assert (
        debate.focus == "Superconducting qubits vs trapped ions: commercial scalability"
    )
    assert debate.roles == ["Superconducting Physicist", "Ion Trap Researcher"]
    assert debate.length == "long"

    article = recipe.podcasts[2]
    assert isinstance(article, TopicArticleDescriptor)
    assert article.type == "TopicArticle"
    assert article.focus == "Neutral Atom Breakthrough Paper"
    assert article.roles == ["Tech Journalist", "Lead Author"]
    assert article.source_id == "src-123"


def test_recipe_rejects_type_aliases():
    for alias in ("deep_dive", "debate", "article", "main_article_with_author"):
        with pytest.raises(ValidationError):
            TopicWorkflowRecipe.model_validate(
                {
                    "research": {"query": "AI Agents"},
                    "podcasts": [{"type": alias, "focus": "Testing"}],
                }
            )


def test_recipe_requires_at_least_one_podcast():
    with pytest.raises(ValidationError):
        TopicWorkflowRecipe.model_validate(
            {
                "research": {"query": "Test"},
                "podcasts": [],
            }
        )


def test_recipe_forbids_extra_fields():
    with pytest.raises(ValidationError):
        TopicWorkflowRecipe.model_validate(
            {
                "research": {"query": "Test", "unknown_field": 123},
                "podcasts": [{"type": "TopicDeepDive"}],
            }
        )


def test_roles_must_be_array_of_exactly_two_elements():
    # 1 element -> fails
    with pytest.raises(ValidationError):
        TopicDebateDescriptor(type="TopicDebate", roles=["Only One Role"])

    # 3 elements -> fails
    with pytest.raises(ValidationError):
        TopicDebateDescriptor(type="TopicDebate", roles=["Role 1", "Role 2", "Role 3"])

    # 2 elements -> succeeds
    desc = TopicDebateDescriptor(type="TopicDebate", roles=["Role 1", "Role 2"])
    assert desc.roles == ["Role 1", "Role 2"]

    # None -> succeeds (optional)
    desc_none = TopicDebateDescriptor(type="TopicDebate")
    assert desc_none.roles is None
