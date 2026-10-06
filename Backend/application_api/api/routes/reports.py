"""Report routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import Response as FastAPIResponse
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.services.fencing import fenced_db
from application_api.repositories import reports as repo
from application_api.schemas.reports import (
    ReportCreate,
    ReportImageIn,
    ReportImageOut,
    ReportOut,
    ReportPublish,
    ReportSummaryOut,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("/", response_model=ReportOut, status_code=201)
async def create_report(data: ReportCreate, response: Response, db: AsyncSession = Depends(fenced_db)):
    """Create a new report row with initial source content."""
    report, created = await repo.create_report(db, data)
    if not created:
        response.status_code = 200
    return report


@router.get("/session/{session_id}", response_model=list[ReportSummaryOut])
async def list_session_reports(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """List all reports for a session (no source_text in the list response)."""
    return await repo.list_session_reports(db, session_id)


@router.get("/{report_id}", response_model=ReportOut)
async def read_report(report_id: UUID, db: AsyncSession = Depends(get_db)):
    """Return a report with its registered image set."""
    return await repo.get_report(db, report_id)


@router.post("/{report_id}/images", response_model=ReportImageOut, status_code=201)
async def register_image(
    report_id: UUID, data: ReportImageIn, db: AsyncSession = Depends(fenced_db)
):
    """Register a figure artifact as a named source image for a report."""
    return await repo.register_image(db, report_id, data)


@router.post("/{report_id}/publish")
async def publish_report(
    report_id: UUID,
    data: ReportPublish,
    db: AsyncSession = Depends(fenced_db),
):
    """Compile-then-publish."""
    pdf_bytes = await repo.publish_report(db, report_id, data)
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report_{report_id}.pdf"',
            "X-Report-Id": str(report_id),
        },
    )
