"""Hooks of ``relevance_ranker`` (see ``toolkit/agents/loader.py``).

Its answer is ``ScoredPaper`` (one paper's score, tier and annotations), one of the literature
review's step contracts in ``literature_review_agent/contracts.py``.
"""

from __future__ import annotations

from ..literature_review_agent.contracts import ScoredPaper

output_contract = ScoredPaper
