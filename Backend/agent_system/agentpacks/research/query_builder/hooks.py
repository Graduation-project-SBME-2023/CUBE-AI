"""Hooks of ``query_builder`` (see ``toolkit/agents/loader.py``).

Its answer is ``QueryPlan`` (the search queries, by database), one of the literature
review's step contracts in ``literature_review_agent/contracts.py``.
"""

from __future__ import annotations

from ..literature_review_agent.contracts import QueryPlan

output_contract = QueryPlan
