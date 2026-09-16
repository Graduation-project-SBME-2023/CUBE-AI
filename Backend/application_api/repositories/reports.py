"""Reports repository"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.artifacts import Artifact
from application_api.db.models.reports import Report, ReportImage
from application_api.schemas import reports as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)

# Maximum LaTeX source size accepted (1 MiB).
MAX_SOURCE_BYTES = 1024 * 1024
# pdflatex hard timeout (seconds).
COMPILE_TIMEOUT = 60.0


# ── helpers ───────────────────────────────────────────────────────────────────

def _report_out(report: Report, images: list[ReportImage]) -> dict[str, Any]:
    return {
        "report_id": report.report_id,
        "session_id": report.session_id,
        "created_by_agent_run_id": report.created_by_agent_run_id,
        "literature_review_id": report.literature_review_id,
        "source_text": report.source_text,
        "source_format": report.source_format,
        "source_version": report.source_version,
        "title": report.title,
        "schema_version": report.schema_version,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "images": [_image_out(i) for i in images],
    }


def _image_out(image: ReportImage) -> dict[str, Any]:
    return {
        "image_id": image.image_id,
        "report_id": image.report_id,
        "artifact_id": image.artifact_id,
        "image_key": image.image_key,
    }


async def _load_images(db: AsyncSession, report_id: UUID) -> list[ReportImage]:
    result = await db.execute(
        select(ReportImage)
        .where(ReportImage.report_id == report_id)
        .order_by(ReportImage.image_key)
    )
    return list(result.scalars().all())


async def _get_report_entity(db: AsyncSession, report_id: UUID) -> Report:
    report = (
        await db.execute(select(Report).where(Report.report_id == report_id))
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return report


# ── reads ─────────────────────────────────────────────────────────────────────

async def get_report(db: AsyncSession, report_id: UUID) -> dict[str, Any]:
    report = await _get_report_entity(db, report_id)
    images = await _load_images(db, report_id)
    return _report_out(report, images)


async def list_session_reports(
    db: AsyncSession, session_id: UUID
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(
            Report.report_id,
            Report.session_id,
            Report.created_by_agent_run_id,
            Report.literature_review_id,
            Report.source_format,
            Report.source_version,
            Report.title,
            Report.created_at,
            Report.updated_at,
        )
        .where(Report.session_id == session_id)
        .order_by(Report.created_at)
    )
    return [dict(row._mapping) for row in result]


# ── create ────────────────────────────────────────────────────────────────────

async def create_report(
    db: AsyncSession, data: schemas.ReportCreate
) -> tuple[dict[str, Any], bool]:
    """Create a report, or return an existing one (insert-or-read by source)."""
    if len(data.source_text.encode()) > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"source_text exceeds the {MAX_SOURCE_BYTES // 1024} KiB limit",
        )

    # Check for existing report by the natural key.
    existing: Report | None = None
    if data.created_by_agent_run_id is not None:
        existing = (
            await db.execute(
                select(Report).where(
                    Report.created_by_agent_run_id == data.created_by_agent_run_id
                )
            )
        ).scalar_one_or_none()
    elif data.literature_review_id is not None:
        existing = (
            await db.execute(
                select(Report).where(
                    Report.literature_review_id == data.literature_review_id
                )
            )
        ).scalar_one_or_none()

    if existing is not None:
        images = await _load_images(db, existing.report_id)
        return _report_out(existing, images), False

    report = Report(
        session_id=data.session_id,
        created_by_agent_run_id=data.created_by_agent_run_id,
        literature_review_id=data.literature_review_id,
        source_text=data.source_text,
        source_format=data.source_format,
        title=data.title,
    )
    db.add(report)
    try:
        async with publication(db, data.session_id) as publish:
            await db.flush()
            publish(
                "report_created",
                {
                    "schema_version": 1,
                    "report_id": str(report.report_id),
                    "session_id": str(data.session_id),
                    "source_format": data.source_format,
                },
            )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Report rejected: {exc.orig}"
        ) from exc

    images = await _load_images(db, report.report_id)
    return _report_out(report, images), True


# ── register image ────────────────────────────────────────────────────────────

async def register_image(
    db: AsyncSession, report_id: UUID, data: schemas.ReportImageIn
) -> dict[str, Any]:
    """Register one artifact as a named source image for a report."""
    report = await _get_report_entity(db, report_id)

    # Verify the artifact exists, belongs to the same session, and is an image.
    artifact = (
        await db.execute(
            select(Artifact.artifact_id, Artifact.session_id, Artifact.kind).where(
                Artifact.artifact_id == data.artifact_id
            )
        )
    ).first()
    if artifact is None:
        raise HTTPException(status_code=422, detail=f"Artifact {data.artifact_id} not found")
    if artifact.session_id != report.session_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Artifact {data.artifact_id} belongs to session {artifact.session_id}, "
                f"not {report.session_id} (R1)"
            ),
        )
    if artifact.kind not in ("figure_png", "figure_jpeg"):
        raise HTTPException(
            status_code=422,
            detail=f"Artifact {data.artifact_id} has kind {artifact.kind!r}; "
                   "must be figure_png or figure_jpeg",
        )

    image = ReportImage(
        report_id=report_id,
        artifact_id=data.artifact_id,
        image_key=data.image_key,
    )
    db.add(image)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Image key {data.image_key!r} is already registered for this report",
        ) from exc
    await db.refresh(image)
    return _image_out(image)


# ── compile-then-publish ────────────────────────────────────────────────

def _compile_latex(source_text: str, image_paths: dict[str, bytes]) -> bytes:
    """Compile LaTeX in an isolated temp directory. Returns PDF bytes."""
    try:
        from storage_paths import TEMP_DIR  # type: ignore[import]
        latex_root = TEMP_DIR / "latex"
    except Exception:
        latex_root = Path(tempfile.gettempdir()) / "cube_latex"

    latex_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=latex_root) as tmp:
        tmp_path = Path(tmp)
        tex_path = tmp_path / "document.tex"
        tex_path.write_text(source_text, encoding="utf-8")

        # Write registered images into the temp dir so \includegraphics works.
        for key, content in image_paths.items():
            # image_key is already validated as safe (no traversal).
            dest = tmp_path / key
            dest.write_bytes(content)

        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", str(tex_path)],
            cwd=tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIMEOUT,
        )

        if result.returncode != 0 or not (tmp_path / "document.pdf").exists():
            stdout = result.stdout.decode("utf-8", errors="replace")[-4000:]
            stderr = result.stderr.decode("utf-8", errors="replace")[-2000:]
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "latex_compile_error",
                    "message": "LaTeX compilation failed; source_text unchanged (A19)",
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )

        return (tmp_path / "document.pdf").read_bytes()


async def publish_report(
    db: AsyncSession,
    report_id: UUID,
    data: schemas.ReportPublish,
) -> bytes:
    """Compile-then-publish."""
    if len(data.latex_text.encode()) > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"latex_text exceeds the {MAX_SOURCE_BYTES // 1024} KiB limit",
        )

    # Step 1: lock and verify version.
    row = (
        await db.execute(
            text(
                "SELECT report_id, session_id, source_version "
                "FROM reports WHERE report_id = :rid FOR UPDATE"
            ),
            {"rid": str(report_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    session_id = row.session_id
    current_version: int = row.source_version

    if current_version != data.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_version",
                "message": (
                    f"Report {report_id} is at version {current_version}; "
                    f"expected {data.expected_version}. The first successful "
                    "publication wins; rebase your changes and retry (A19)."
                ),
                "current_version": current_version,
            },
        )

    # Step 2: validate image keys.
    declared_keys = set(data.image_keys)
    if declared_keys:
        registered_rows = (
            await db.execute(
                select(
                    ReportImage.image_key,
                    ReportImage.artifact_id,
                    Artifact.session_id,
                    Artifact.kind,
                    Artifact.body,
                )
                .join(Artifact, Artifact.artifact_id == ReportImage.artifact_id)
                .where(
                    ReportImage.report_id == report_id,
                    ReportImage.image_key.in_(declared_keys),
                )
            )
        ).all()
        found_keys = {r.image_key for r in registered_rows}
        missing = declared_keys - found_keys
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{len(missing)} image key(s) are not registered for this report: "
                    f"{sorted(missing)[:10]}"
                ),
            )
        wrong_session = [
            r.image_key for r in registered_rows if r.session_id != session_id
        ]
        wrong_kind = [
            r.image_key
            for r in registered_rows
            if r.kind not in ("figure_png", "figure_jpeg")
        ]
        if wrong_session:
            raise HTTPException(
                status_code=409,
                detail=f"Image(s) belong to another session (R1): {wrong_session[:5]}",
            )
        if wrong_kind:
            raise HTTPException(
                status_code=422,
                detail=f"Image(s) have wrong kind (must be figure_png/jpeg): {wrong_kind[:5]}",
            )

        # Step 3: collect image bytes.
        image_paths: dict[str, bytes] = {}
        for r in registered_rows:
            if r.body:
                image_paths[r.image_key] = bytes(r.body)
    else:
        registered_rows = []
        image_paths = {}

    # Step 4: compile.  Raises 422 on error; no row is touched before this point.
    pdf_bytes = _compile_latex(data.latex_text, image_paths)

    # Step 5: commit source + version under the session lock.
    async with publication(db, session_id) as publish:
        await db.execute(
            text(
                "UPDATE reports "
                "SET source_text = :source_text, "
                "    source_version = source_version + 1, "
                "    updated_at = now() "
                "WHERE report_id = :rid "
                "  AND source_version = :expected_version"
            ),
            {
                "source_text": data.latex_text,
                "rid": str(report_id),
                "expected_version": data.expected_version,
            },
        )
        publish(
            "report_published",
            {
                "schema_version": 1,
                "report_id": str(report_id),
                "session_id": str(session_id),
                "new_version": current_version + 1,
            },
        )

    logger.info(
        "Report %s published to version %d (%d PDF bytes)",
        report_id,
        current_version + 1,
        len(pdf_bytes),
    )
    # Step 6: return PDF bytes for streaming; no durable file.
    return pdf_bytes
