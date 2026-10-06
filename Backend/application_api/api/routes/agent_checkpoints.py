from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import agent_checkpoints as checkpoints_repo
from application_api.schemas.agent_checkpoints import PurgeOut

router = APIRouter(prefix="/agent-checkpoints", tags=["Agent checkpoints"])


@router.post("/purge", response_model=PurgeOut)
async def purge_checkpoints(batch: int = Query(500, ge=1, le=5000), db: AsyncSession = Depends(get_db)):
    """Retention purge (harness path), one batch: the checkpoints of runs
    terminal for longer than AGENT_CHECKPOINT_RETENTION_DAYS that no live
    seed_from names. The lifespan sweep (`services/retention.py`) runs it on
    a timer; this route triggers a batch by hand."""
    return await checkpoints_repo.purge_checkpoints(db, batch)
