"""Manual LaTeX preview compilation for the Reporting tab."""

import base64
import os
import re
import subprocess
import tempfile

from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from storage_paths import TEMP_DIR

router = APIRouter()


@router.post("/reports/compile")
async def compile_report(data: dict, request: Request):
    """Compile LaTeX text to a PDF and return it inline (A19 — no durable PDF).

    This is the manual "preview compile" in the Reporting tab, independent of
    the pipeline's own publish flow (POST /reports/{id}/publish via the
    Application API). It used to copy the compiled PDF into the session
    workspace under outputs/reports/ and hand back a file URL — that path was
    retired by storage_write_guard (§8; no file fallback for report PDFs), so
    every compile here now raises RetiredWorkspaceWrite instead of returning a
    result. Compiling only in TEMP_DIR (disposable, unguarded) and returning
    the bytes as a data URI keeps the existing fetch(pdf_url)-then-blob
    frontend contract working with no durable file anywhere.
    """
    session_id = str(data.get("session_id") or "").strip()
    latex_text = data.get("latex_text")
    output_filename = str(data.get("output_filename") or "final_report.pdf").strip()

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if not isinstance(latex_text, str) or not latex_text.strip():
        raise HTTPException(status_code=400, detail="latex_text is required")

    # Ensure filename cannot traverse directories and always ends in .pdf
    safe_name = os.path.basename(output_filename)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"

    latex_temp_root = TEMP_DIR / "latex"
    latex_temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=latex_temp_root) as temp_dir:
        tex_path = os.path.join(temp_dir, "document.tex")
        pdf_temp_path = os.path.join(temp_dir, "document.pdf")

        with open(tex_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(latex_text)

        compile_logs = []
        for _ in range(2):
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "document.tex"],
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30.0,
            )
            stdout = result.stdout.decode("utf-8", errors="ignore")
            stderr = result.stderr.decode("utf-8", errors="ignore")
            compile_logs.append(stdout)
            if stderr:
                compile_logs.append(stderr)
            if result.returncode != 0:
                break

        if not os.path.exists(pdf_temp_path):
            merged_log = "\n".join(compile_logs)[-8000:]
            return {
                "status": "error",
                "message": "LaTeX compilation failed",
                "compile_log": merged_log,
            }

        with open(pdf_temp_path, "rb") as src:
            pdf_bytes = src.read()

    pdf_url = f"data:application/pdf;base64,{base64.b64encode(pdf_bytes).decode('ascii')}"

    return {
        "status": "success",
        "output_filename": safe_name,
        "pdf_url": pdf_url,
    }
