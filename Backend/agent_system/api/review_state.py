"""Segmentation review wake-up state, shared by several routers.

Both dicts are mutated in place and never rebound, so importers share them.
"""

import asyncio
from typing import Dict

# ── Review wake-ups ──────────────────────────────────────────────────────────
# The review itself is a row (segmentation_runs.review_status='submitted'); this
# in-process event only wakes a waiting module sooner. A restart loses the event
# and nothing else: the wait endpoint re-reads PostgreSQL.
review_events: Dict[str, asyncio.Event] = {}
review_futures: Dict[str, asyncio.Future] = {}
