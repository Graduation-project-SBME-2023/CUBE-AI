from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from uuid import UUID

from models.Wslog import WSLog as WSLogModel
from schemas.Wslog import *


# ---------------------------
# CREATE WS LOG
# ---------------------------
async def create_ws_log(db: AsyncSession, data: WSLogCreate):
    try:
        log = WSLogModel(
            session_id=data.session_id,
            msg_type=data.msg_type,
            payload=data.payload
        )

        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------
# GET WS LOG
# ---------------------------
async def get_ws_log(db: AsyncSession, log_id: UUID):
    result = await db.execute(
        select(WSLogModel).where(WSLogModel.uuid == log_id)
    )
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="WS log not found")

    return log


# ---------------------------
# GET LOGS WITH FILTERS
# ---------------------------
async def get_logs(
    db: AsyncSession,
    session_id: UUID = None,
    msg_type: str = None,
    json_key: str = None,
    json_value: str = None,
):
    try:
        query = select(WSLogModel)

        if session_id:
            query = query.where(WSLogModel.session_id == session_id)

        if msg_type:
            query = query.where(WSLogModel.msg_type == msg_type)

        # JSON search
        if json_key:
            if json_value:
                query = query.where(
                    WSLogModel.payload[json_key].astext == json_value
                )
            else:
                query = query.where(
                    WSLogModel.payload.has_key(json_key)  # noqa
                )

        query = query.order_by(WSLogModel.created_at.asc())

        result = await db.execute(query)
        return result.scalars().all()

    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------
# UPDATE WS LOG
# ---------------------------
async def update_ws_log(db: AsyncSession, log_id: UUID, data:   WSLogUpdate):
    try:
        log = await get_ws_log(db, log_id)

        update_data = data.dict(exclude_unset=True)

        for field, value in update_data.items():
            setattr(log, field, value)

        await db.commit()
        await db.refresh(log)
        return log

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------
# APPEND TO PAYLOAD
# ---------------------------
async def append_ws_payload(db: AsyncSession, log_id: UUID, new_data: dict):
    try:
        log = await get_ws_log(db, log_id)

        if log.payload is None:
            log.payload = new_data
        elif isinstance(log.payload, dict):
            log.payload = {**log.payload, **new_data}
        else:
            raise HTTPException(
                status_code=400,
                detail="Payload is not JSON object"
            )

        await db.commit()
        await db.refresh(log)
        return log

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------
# DELETE LOG
# ---------------------------
async def delete_ws_log(db: AsyncSession, log_id: UUID):
    try:
        log = await get_ws_log(db, log_id)
        await db.delete(log)
        await db.commit()
        return {"detail": "Deleted"}

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------
# GET ALL LOGS
# ---------------------------
async def get_all_logs(db: AsyncSession):
    try:
        result = await db.execute(
            select(WSLogModel).order_by(WSLogModel.created_at.desc())
        )
        return result.scalars().all()

    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
# ---------------------------
# DELETE LOGS BY SESSION
# ---------------------------
async def delete_logs_by_session(db: AsyncSession, session_id: UUID):
    try:
        logs = await get_logs(db, session_id=session_id)

        for log in logs:
            await db.delete(log)

        await db.commit()
        return {"detail": "All session logs deleted"}

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))