"""Hooks of ``pico_analyst`` (see ``toolkit/agents/loader.py``).

Its answer is ``PicoAnalysis`` (the research dimensions, MeSH terms and seed keywords), one of the literature
review's step contracts in ``literature_review_agent/contracts.py``.
"""

from __future__ import annotations

from ..literature_review_agent.contracts import PicoAnalysis

output_contract = PicoAnalysis
