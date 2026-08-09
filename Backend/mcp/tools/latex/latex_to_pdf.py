import subprocess
import tempfile
import shutil
import glob
import sys
import os
import asyncio
import json
from .. import mcp
from pydantic import Field
from typing import Annotated
from tool_logging import ToolEvent, log_tool_state, TextPayload
from storage_paths import HYPOTHESES_DIR, TEMP_DIR, workspace_subpath

@mcp.tool()
async def latex_to_pdf(
    latex_text: Annotated[str, Field(description="The complete LaTeX source code content, including the preamble and document body, to be compiled.")] ,
    output_filename: Annotated[str, Field(description="The file name where the generated PDF should be saved, without the path (e.g., 'report.pdf').")],
) -> str: 
    """
    Compiles LaTeX source code into a PDF document using pdflatex.

    Use this tool in scenarios such as:
    1. When you need to convert generated LaTeX content into a final, downloadable PDF file.
    2. To save a formatted report or summary to the local file system.

    Images: Any .png or .jpg files found in the workspace (features/, modeling/,
    outputs/ subdirectories) are automatically copied into the compilation
    directory. Use \\includegraphics{filename.png} (basename only, no path)
    in your LaTeX source to embed them.
    """
    try:
        latex_temp_root = TEMP_DIR / "latex"
        latex_temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=latex_temp_root) as temp:
            payload = TextPayload(message='Generating pdf...')
            start_event = ToolEvent('tool_started', 'text', payload)
            log_tool_state(start_event)
            log_event = ToolEvent('tool_progress', 'text', payload)
            log_tool_state(log_event)
            tex_path = os.path.join(temp, "document.tex")

            # ── Copy workspace images into the temp dir so \includegraphics works ──
            workspace_folder = os.environ.get("WORKSPACE_DIR", "")
            if workspace_folder and os.path.isdir(workspace_folder):
                copied_images = []
                for ext in ("*.png", "*.jpg", "*.jpeg"):
                    for img_path in glob.glob(
                        os.path.join(workspace_folder, "**", ext), recursive=True
                    ):
                        basename = os.path.basename(img_path)
                        dest = os.path.join(temp, basename)
                        # Avoid overwriting if two images share the same name
                        if not os.path.exists(dest):
                            shutil.copy2(img_path, dest)
                            copied_images.append(basename)
                if copied_images:
                    payload = TextPayload(
                        message=f"Copied {len(copied_images)} images into compilation directory."
                    )
                    log_tool_state(ToolEvent("tool_progress", "text", payload))

            # Write LaTeX to .tex file
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(latex_text)

            payload = TextPayload(message='Compiling...')
            compiling_latex_event = ToolEvent('tool_progress', 'text', payload)
            log_tool_state(compiling_latex_event)
            # Compile using pdflatex
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", tex_path],
                cwd=temp,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20.0
            )

            # Check for compilation errors
            if result.returncode != 0:
                payload = TextPayload(message='LaTeX compilation failed.')
                error_compilation_event = ToolEvent('error', 'text', payload)
                log_tool_state(error_compilation_event)

                stdout = result.stdout.decode("utf-8", errors="ignore")
                stderr = result.stderr.decode("utf-8", errors="ignore")
                return json.dumps(
                    {
                        "status": "error",
                        "message": "LaTeX compilation failed",
                        "stdout": stdout[-4000:],
                        "stderr": stderr[-2000:],
                    }
                )

            # Read compiled PDF bytes
            pdf_path = os.path.join(temp, "document.pdf")

            if not os.path.exists(pdf_path):
                payload = TextPayload(message='Possible LaTeX error.')
                error_compilation_event = ToolEvent('error', 'text', payload)
                log_tool_state(error_compilation_event)
                return json.dumps(
                    {
                        "status": "error",
                        "message": "PDF was not generated. Possible LaTeX error.",
                    }
                )

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

        workspace_folder = os.environ.get("WORKSPACE_DIR")
        if not workspace_folder:
            target_dir = str(HYPOTHESES_DIR / "workspace_default")
            final_folder = str(workspace_subpath(target_dir, "reports"))
            workspace_folder_name = os.path.basename(target_dir)
        else:
            final_folder = str(workspace_subpath(workspace_folder, "reports"))
            workspace_folder_name = os.path.basename(workspace_folder)

        os.makedirs(final_folder, exist_ok=True)
        final_path = os.path.join(final_folder, output_filename)
                
        # Save final PDF
        with open(final_path, "wb") as f:
            f.write(pdf_bytes)
        payload = TextPayload(message='PDF generated successfully.')
        error_compilation_event = ToolEvent('tool_finished', 'text', payload)
        log_tool_state(error_compilation_event)

        public_name = output_filename if output_filename.endswith('.pdf') else f"{output_filename}.pdf"
        # Build a public URL using env var AGENTS_BASE_URL when available.
        agents_base = os.environ.get('AGENTS_BASE_URL')
        if agents_base:
            latex_pdf = agents_base.rstrip('/') + f"/files/{workspace_folder_name}/reports/{public_name}"
        else:
            # Fallback to a relative path that can be resolved by the orchestrator when serving files
            latex_pdf = f"/files/{workspace_folder_name}/reports/{public_name}"

        return json.dumps(
            {
                "status": "success",
                "message": "PDF generated successfully",
                "latex_text": latex_text,
                "pdf_path": final_path,
                "latex_pdf": latex_pdf,
            }
        )

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

if __name__ == "__main__":
    # 1. Simple LaTeX content for testing
    test_latex = r"""
    \documentclass{article}
    \title{Test Document}
    \author{Python Script}
    \date{\today}
    \begin{document}
    \maketitle
    \section{Introduction}
    This is a test PDF generated from Python code.
    \end{document}
    """

    # 2. Run the async function
    print("Starting PDF generation...")
    result = asyncio.run(latex_to_pdf(test_latex, "test_doc.pdf"))
    
    # 3. Print result
    print(result)
