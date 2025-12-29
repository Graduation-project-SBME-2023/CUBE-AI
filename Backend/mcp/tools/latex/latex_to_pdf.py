import subprocess
import tempfile
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
import asyncio
# from .. import mcp
from pydantic import Field
from typing import Annotated
from tool_logging import ToolEvent, log_tool_state, TextPayload

# @mcp.tool()
async def latex_to_pdf(
    latex_text: Annotated[str, Field(description="The complete LaTeX source code content, including the preamble and document body, to be compiled.")] ,
    output_filename: Annotated[str, Field(description="The file name where the generated PDF should be saved, without the path (e.g., 'report.pdf').")]
) -> str: 
    """
    Compiles LaTeX source code into a PDF document using pdflatex.

    Use this tool in scenarios such as:
    1. When you need to convert generated LaTeX content into a final, downloadable PDF file.
    2. To save a formatted report or summary to the local file system.
    """
    try:
        with tempfile.TemporaryDirectory() as temp:
            payload = TextPayload(message='Generating pdf...')
            start_event = ToolEvent('tool_started', 'latex_reporter_agent', 'text', payload)
            log_tool_state(start_event)
            tex_path = os.path.join(temp, "document.tex")

            # Write LaTeX to .tex file
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(latex_text)

            payload = TextPayload(message='Compiling...')
            compiling_latex_event = ToolEvent('tool_progress', 'latex_reporter_agent', 'text', payload)
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
                error_compilation_event = ToolEvent('error','latex_reporter_agent', 'text', payload)
                log_tool_state(error_compilation_event)
                return "LaTeX compilation failed. Check your LaTeX syntax."

            # Read compiled PDF bytes
            pdf_path = os.path.join(temp, "document.pdf")

            if not os.path.exists(pdf_path):
                payload = TextPayload(message='Possible LaTeX error.')
                error_compilation_event = ToolEvent('error','latex_reporter_agent', 'text', payload)
                log_tool_state(error_compilation_event)
                return "PDF was not generated. Possible LaTeX error."

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

    
        current_dir = os.getcwd()
        target_dir = os.path.abspath(os.path.join(current_dir, "Backend", "agent_system/workspace"))
        final_folder = os.path.join(target_dir, "reports") 
        final_path = os.path.join(final_folder, output_filename)
                
        # Save final PDF
        with open(final_path, "wb") as f:
            f.write(pdf_bytes)
        payload = TextPayload(message='PDF generated successfully.')
        error_compilation_event = ToolEvent('tool_finished','latex_reporter_agent', 'text', payload)
        log_tool_state(error_compilation_event)
        return f"PDF generated successfully and saved as: {output_filename}"

    except Exception as e:
        return f"Error: {str(e)}"






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