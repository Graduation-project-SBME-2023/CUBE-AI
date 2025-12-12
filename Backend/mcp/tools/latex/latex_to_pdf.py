import subprocess
import tempfile
import os 
from .. import mcp
from pydantic import Field
from typing import Annotated

@mcp.tool()
async def latex_to_pdf(
    latex_text: Annotated[str, Field(description="The complete LaTeX source code content, including the preamble and document body, to be compiled.")] ,
    output_filename: Annotated[str, Field(description="The destination file path or name where the generated PDF should be saved (e.g., 'report.pdf').")]
) -> str: 
    """
    Compiles LaTeX source code into a PDF document using pdflatex.

    Use this tool in scenarios such as:
    1. When you need to convert generated LaTeX content into a final, downloadable PDF file.
    2. To save a formatted report or summary to the local file system.
    """
    try:
        with tempfile.TemporaryDirectory() as temp:
            tex_path = os.path.join(temp, "document.tex")

            # Write LaTeX to .tex file
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(latex_text)

            # Compile using pdflatex
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", tex_path],
                cwd=temp,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Check for compilation errors
            if result.returncode != 0:
                return "❌ LaTeX compilation failed. Check your LaTeX syntax."

            # Read compiled PDF bytes
            pdf_path = os.path.join(temp, "document.pdf")

            if not os.path.exists(pdf_path):
                return "❌ PDF was not generated. Possible LaTeX error."

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

        # Save final PDF
        with open(output_filename, "wb") as f:
            f.write(pdf_bytes)

        return f"✅ PDF generated successfully and saved as: {output_filename}"

    except Exception as e:
        return f"❌ Error: {str(e)}"