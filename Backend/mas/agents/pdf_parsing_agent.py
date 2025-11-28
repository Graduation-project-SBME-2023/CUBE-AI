"""
ValidationModule – performs internal / external validation of the study.

It can compute performance metrics (AUC, accuracy, etc.) on a held‑out
cohort, or run cross‑validation, and returns a concise evaluation.
"""

from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.tools.langchain import LangChainToolAdapter
from autogen_core import CancellationToken
from pydantic import BaseModel
from typing import List, Tuple, Union, Dict, Any
from typing import Dict, Any
import pandas as pd
import requests
import datetime
import json
import asyncio
import pypandoc

from pathlib import Path

# def get_final_report():
#     pass
def save_markdown_to_pdf(markdown_text: str, ):
    """
    Convert Markdown string to PDF using pure Python (no LaTeX or Pandoc needed).
    """
    from markdown import markdown
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.units import inch
    from bs4 import BeautifulSoup
    output_path="resulttt.pdf"
    # Convert Markdown → HTML → plain paragraphs
    html = markdown(markdown_text)
    soup = BeautifulSoup(html, "html.parser")
    doc = SimpleDocTemplate(output_path, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    for element in soup.find_all(["h1", "h2", "p", "li"]):
        style = styles["Normal"]
        if element.name == "h1":
            style = styles["Heading1"]
        elif element.name == "h2":
            style = styles["Heading2"]
        story.append(Paragraph(element.get_text(), style))
        story.append(Spacer(1, 0.2 * inch))

    doc.build(story)
    print(f"✅ PDF saved successfully at: {output_path}")


import subprocess
import tempfile
import os

def latex_to_pdf(latex_text: str, output_filename: str) -> str:
    """
    Compiles LaTeX text into a PDF using pdflatex.
    
    Args:
        latex_text (str): LaTeX source code.
        output_filename (str): Name of the final PDF to save.
    
    Returns:
        str: Status message.
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


# def save_markdown_to_pdf(markdown_text: str):
#     """
#     Convert a Markdown string to a PDF file.

#     Parameters
#     ----------
#     markdown_text : str
#         The Markdown content to convert.
#     output_path : str
#         Path where the output PDF will be saved.
#     """
#     output_path = 'resulttt.pdf'
#     try:
#         pypandoc.download_pandoc()
#         pypandoc.convert_text(
#             markdown_text,
#             to='pdf',
#             format='md',
#             outputfile=output_path,
#             extra_args=['--standalone']
#         )
#         print(f"✅ PDF saved successfully at: {output_path}")
#     except Exception as e:
#         print(f"❌ Failed to save PDF: {e}")

def get_final_report() -> str:
    """
    Reads the final report file and returns its content as a formatted string.

    Parameters
    ----------
    file_path : str
        Path to the JSON file.

    Returns
    -------
    str
        The JSON file content as a pretty-printed string.
    """
    current_dir = Path(__file__).resolve().parent

    # Go one level up (parent folder), then into memory/workspace/
    file_path = current_dir.parent / "memory" / "workspace"/"final_report.json"

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"❌ File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return json.dumps(data, indent=2, ensure_ascii=False)



class PdfParsingModule:
    """Placeholder for the validation module."""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        
        self.model_client = OllamaChatCompletionClient(
        model="gpt-oss:120b-cloud",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True
        }
    )

    async def initialize(self) -> bool:
        """Prepare any validation datasets (stub)."""
        return True

    async def run(self):
        """
        Compute validation metrics.

        Returns a dict such as ``{"auc": 0.85, "accuracy": 0.78}``.
        """
        # Stub: return empty metrics
        agent = AssistantAgent(
        name="pdf_maker",
        model_client=self.model_client,
        tools=[get_final_report, latex_to_pdf],
        max_tool_iterations= 5,
        system_message="""your mission is to read the final report , format it in full latex code and then save it in a pdf
        """,
        )

    # # Let the agent translate some text
        await Console(
        agent.run_stream(task="make me a pdf for the final report ",
                         cancellation_token=CancellationToken())
        )
        
        
        
        
        return {}


__all__ = ["ValidationModule"]


async def main():
    pop = PdfParsingModule()
    await pop.run()  # also needs await if run() is async

asyncio.run(main())