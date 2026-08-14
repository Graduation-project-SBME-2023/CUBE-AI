# endpoints/Wslog.py

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from application_api.db.session import get_db
from application_api.schemas.Wslog import WSLog, WSLogCreate, WSLogUpdate
import application_api.repositories.Wslog as wslog_crud   
router = APIRouter(prefix="/ws_logs", tags=["WS Logs"])


@router.post("/", response_model=WSLog, status_code=status.HTTP_201_CREATED)
async def create_ws_log(data: WSLogCreate, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.create_ws_log(db, data)

@router.get("/", response_model=list[WSLog])
async def get_all_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await wslog_crud.get_all_logs(db, skip=skip, limit=limit)

@router.get("/filter", response_model=list[WSLog]) 
async def get_logs(
    session_id: UUID | None = None,
    msg_type: str | None = None,
    json_key: str | None = None,
    json_value: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await wslog_crud.get_logs(db, session_id=session_id, msg_type=msg_type,
                                     json_key=json_key, json_value=json_value,
                                     skip=skip, limit=limit)

@router.get("/{log_id}", response_model=WSLog)
async def get_ws_log(log_id: UUID, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.get_ws_log(db, log_id)

@router.get("/session/{session_id}", response_model=list[WSLog])
async def get_logs_by_session(
    session_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await wslog_crud.get_logs(db, session_id=session_id, skip=skip, limit=limit)

@router.put("/{log_id}", response_model=WSLog)
async def update_ws_log(log_id: UUID, data: WSLogUpdate, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.update_ws_log(db, log_id, data)

@router.patch("/{log_id}/append", response_model=WSLog)
async def append_ws_payload(log_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.append_ws_payload(db, log_id, payload)

@router.delete("/{log_id}", status_code=status.HTTP_200_OK)
async def delete_ws_log(log_id: UUID, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.delete_ws_log(db, log_id)

@router.delete("/session/{session_id}", status_code=status.HTTP_200_OK)
async def delete_logs_by_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.delete_logs_by_session(db, session_id)
