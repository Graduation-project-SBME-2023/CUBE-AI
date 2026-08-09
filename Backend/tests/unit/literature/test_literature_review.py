from __future__ import annotations

import json

import pytest

from agents.literature_review_agent import literature_review_agent as module


PROMPT_BUILDERS = (
    (module.build_pico_analyst_prompt, True, False),
    (module.build_query_builder_prompt, True, True),
    (module.build_search_executor_prompt, False, True),
    (module.build_relevance_ranker_prompt, True, False),
    (module.build_context_compiler_prompt, True, False),
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("builder", "uses_knowledge", "uses_source_guide"),
    PROMPT_BUILDERS,
)
def test_each_literature_agent_prompt_receives_its_required_context(
    builder,
    uses_knowledge,
    uses_source_guide,
):
    prompt = builder(
        "DYNAMIC_KNOWLEDGE_SENTINEL",
        "SOURCE_GUIDE_SENTINEL",
    )

    assert ("DYNAMIC_KNOWLEDGE_SENTINEL" in prompt) is uses_knowledge
    assert ("SOURCE_GUIDE_SENTINEL" in prompt) is uses_source_guide
    assert len(prompt) > 500


@pytest.mark.unit
def test_relevance_scorer_returns_weighted_breakdown():
    paper = {
        "title": "Adult spleen radiomics classification",
        "abstract": (
            "Contrast enhanced computed tomography radiomics predicts "
            "malignant involvement compared with clinical variables."
        ),
    }
    pico = {
        "population": "adult spleen",
        "intervention": "computed tomography radiomics",
        "comparison": "clinical variables",
        "outcome": "malignant involvement",
        "hypothesis": "radiomics predicts malignant classification",
    }

    result = json.loads(
        module.score_paper_relevance(json.dumps(paper), json.dumps(pico))
    )

    assert result["title"] == paper["title"]
    assert 0 < result["relevance_score"] <= 100
    assert set(result["score_breakdown"]) == set(pico)


@pytest.mark.unit
def test_relevance_scorer_handles_invalid_json_without_raising():
    result = json.loads(module.score_paper_relevance("not-json", "{}"))
    assert result["relevance_score"] == 0.0
    assert result["score_breakdown"] == {}
    assert "error" in result
