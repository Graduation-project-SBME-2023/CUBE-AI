"""Hooks of ``search_executor`` (see ``toolkit/agents/loader.py``).

Its answer is ``SourceSearch`` (one database's annotated papers), one of the literature
review's step contracts in ``literature_review_agent/contracts.py``.
"""

from __future__ import annotations

from ..literature_review_agent.contracts import SourceSearch

output_contract = SourceSearch
