"""Request-scoped dependencies shared by the routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.config import settings
from application_api.db.models.chat_messages import ChatMessage
from application_api.db.models.chats import Chat
from application_api.db.models.cohorts import Cohort
from application_api.db.models.feature_sets import FeatureSet
from application_api.db.models.graph_changes import GraphChange
from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.literature import LiteratureReview
from application_api.db.models.model_results import ModelResult
from application_api.db.models.node_runs import NodeRun
from application_api.db.models.radiomics import Radiomics
from application_api.db.models.reports import Report
from application_api.db.models.segmentation_batches import SegmentationBatch
from application_api.db.models.session_events import SessionEvent
from application_api.db.models.stored_files import StoredFile
from application_api.db.binary_session import get_binary_db
from application_api.db.session import get_db
from application_api.repositories.users import require_owner, resolve_user_id


async def current_user(db: AsyncSession = Depends(get_db)) -> UUID:
    """The caller's user id: the configured default user until authentication
    maps a verified login to a username."""
    return await resolve_user_id(db, settings.DEFAULT_USERNAME)


async def owned_session(
    session_id: UUID,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """The `{session_id}` of a session-scoped route, owned by the caller."""
    await require_owner(db, session_id, user_id)
    return session_id


async def _owned(db: AsyncSession, user_id: UUID, query, what: str, key: UUID) -> UUID:
    session_id = (await db.execute(query)).scalar()
    if session_id is None:
        raise HTTPException(status_code=404, detail=f"{what} {key} not found")
    await require_owner(db, session_id, user_id)
    return session_id


async def owned_run(
    run_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    """The session of `{run_id}`, owned by the caller."""
    return await _owned(db, user_id, select(NodeRun.session_id).where(NodeRun.run_id == run_id), "Run", run_id)


async def owned_change(
    change_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(GraphChange.session_id).where(GraphChange.change_id == change_id)
    return await _owned(db, user_id, query, "Graph change", change_id)


async def owned_node(
    node_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(GraphNode.session_id).where(GraphNode.node_id == node_id)
    return await _owned(db, user_id, query, "Node", node_id)


async def owned_chat(
    chat_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(Chat.session_id).where(Chat.chat_id == chat_id)
    return await _owned(db, user_id, query, "Chat", chat_id)


async def owned_message(
    message_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(Chat.session_id).join(ChatMessage, ChatMessage.chat_id == Chat.chat_id).where(
        ChatMessage.message_id == message_id
    )
    return await _owned(db, user_id, query, "Chat message", message_id)


# Domain rows. The four a run produces reach their session through
# `created_by_run_id`; the others carry `session_id` themselves.
def _through_run(model, key_column, key: UUID):
    return select(NodeRun.session_id).join(model, model.created_by_run_id == NodeRun.run_id).where(
        key_column == key
    )


async def owned_cohort(
    cohort_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = _through_run(Cohort, Cohort.cohort_id, cohort_id)
    return await _owned(db, user_id, query, "Cohort", cohort_id)


async def owned_feature_set(
    feature_set_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = _through_run(FeatureSet, FeatureSet.feature_set_id, feature_set_id)
    return await _owned(db, user_id, query, "Feature set", feature_set_id)


async def owned_model_result(
    model_result_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = _through_run(ModelResult, ModelResult.model_result_id, model_result_id)
    return await _owned(db, user_id, query, "Model result", model_result_id)


async def owned_segmentation_batch(
    segmentation_batch_id: UUID,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    query = _through_run(
        SegmentationBatch, SegmentationBatch.segmentation_batch_id, segmentation_batch_id
    )
    return await _owned(db, user_id, query, "Segmentation batch", segmentation_batch_id)


async def owned_review(
    review_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(LiteratureReview.session_id).where(LiteratureReview.literature_review_id == review_id)
    return await _owned(db, user_id, query, "Literature review", review_id)


async def owned_radiomics(
    radiomics_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(Radiomics.session_id).where(Radiomics.radiomics_feature_id == radiomics_id)
    return await _owned(db, user_id, query, "Radiomics row", radiomics_id)


async def owned_report(
    report_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(Report.session_id).where(Report.report_id == report_id)
    return await _owned(db, user_id, query, "Report", report_id)


async def owned_file(
    file_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(StoredFile.session_id).where(StoredFile.file_id == file_id)
    return await _owned(db, user_id, query, "File", file_id)


async def owned_event(
    event_id: int, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(SessionEvent.session_id).where(SessionEvent.event_id == event_id)
    return await _owned(db, user_id, query, "Session event", event_id)


async def owned_file_content(
    file_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_binary_db)
) -> UUID:
    """`owned_file` on the binary pool, so a download holds no main-pool connection."""
    query = select(StoredFile.session_id).where(StoredFile.file_id == file_id)
    return await _owned(db, user_id, query, "File", file_id)
