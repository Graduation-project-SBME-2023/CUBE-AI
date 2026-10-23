"""Hooks of ``context_compiler`` (see ``toolkit/agents/loader.py``).

Its answer is ``CompiledContext`` (the saved review files and their counts), one of the literature
review's step contracts in ``literature_review_agent/contracts.py``.
"""

from __future__ import annotations

from ..literature_review_agent.contracts import CompiledContext

output_contract = CompiledContext
