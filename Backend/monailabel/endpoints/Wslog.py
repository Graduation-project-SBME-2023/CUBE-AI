# endpoints/Wslog.py

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.database import get_db
from schemas.Wslog import WSLog, WSLogCreate, WSLogUpdate
import crud.Wslog as wslog_crud   
router = APIRouter(prefix="/ws_logs", tags=["WS Logs"])


@router.post("/", response_model=WSLog, status_code=status.HTTP_201_CREATED)
async def create_ws_log(data: WSLogCreate, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.create_ws_log(db, data)

@router.get("/", response_model=list[WSLog])
async def get_all_logs(db: AsyncSession = Depends(get_db)):
    return await wslog_crud.get_all_logs(db)

@router.get("/filter", response_model=list[WSLog]) 
async def get_logs(
    session_id: UUID | None = None,
    msg_type: str | None = None,
    json_key: str | None = None,
    json_value: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await wslog_crud.get_logs(db, session_id=session_id, msg_type=msg_type,
                                     json_key=json_key, json_value=json_value)

@router.get("/{log_id}", response_model=WSLog)
async def get_ws_log(log_id: UUID, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.get_ws_log(db, log_id)

@router.get("/session/{session_id}", response_model=list[WSLog])
async def get_logs_by_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.get_logs(db, session_id=session_id)

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