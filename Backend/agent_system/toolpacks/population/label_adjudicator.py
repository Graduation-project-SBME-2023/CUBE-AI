"""The LLM Clinical Adjudicator: one clinical record in, one label verdict out.

A verdict is ``{"has_target_disease": true | false | null, "confidence": ...,
"source_evidence": ...}``. An answer that is not that JSON is asked for once
more; a second failure is "not labelled", never a negative finding.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

CONFIDENCES = frozenset({"high", "medium", "low", "unlabeled"})

_OUTPUT_FORMAT = (
    "Output pure JSON only, in this exact format:\n"
    "{\n"
    '  "has_target_disease": true, false, or null,\n'
    '  "confidence": "high" | "medium" | "low" | "unlabeled",\n'
    '  "source_evidence": "Detailed reasoning here"\n'
    "}\n"
    "Do not include Markdown fences."
)
_RETRY = (
    "Your response was not valid JSON or lacked mandatory source_evidence. "
    "Return ONLY the JSON object with has_target_disease, confidence, and source_evidence. "
    "Do not include any other text."
)
FAILED_VERDICT: dict[str, Any] = {
    "has_target_disease": None,
    "confidence": "unlabeled",
    "requires_manual_review": True,
    "failure": "invalid_llm_response",
    "source_evidence": "LLM extraction failed: invalid JSON response",
}


def criteria_prompt(inclusion: list[Any], exclusion: list[Any]) -> str:
    """The study criteria and the labelling rules the adjudicator follows."""
    return (
        "You are evaluating clinical records against the following study criteria:\n"
        f"INCLUSION CRITERIA: {json.dumps(inclusion)}\n"
        f"EXCLUSION CRITERIA: {json.dumps(exclusion)}\n\n"
        "You must determine if the patient has the target disease/condition specified "
        "in the inclusion criteria. Follow these strict labeling rules:\n"
        "1. If the medical report is relevant to the target organ/condition and confirms the presence of the target disease, output 'has_target_disease': true.\n"
        "2. If the medical report is relevant to the target organ/condition and explicitly confirms the target disease is absent (e.g., normal or healthy reference findings for that organ), output 'has_target_disease': false.\n"
        "3. If the medical report discusses an unrelated organ or body region, or completely lacks relevant clinical information regarding the target disease/hypothesis, do NOT label it! Leave it unlabeled by outputting 'has_target_disease': null, set 'confidence': 'unlabeled', and clearly document in 'source_evidence' why the report is unrelated or lacks sufficient clinical evidence."
    )


def parse_verdict(response_text: str | None) -> dict[str, Any] | None:
    """The verdict in an answer (Markdown fences tolerated), or ``None`` when
    it is not JSON or has no source evidence."""
    text = (response_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not str(parsed.get("source_evidence") or "").strip():
        return None
    return parsed


def normalized_label(verdict: dict[str, Any]) -> tuple[bool | None, str]:
    """``(has_target_disease, confidence)``: a boolean or ``None``, and a known
    confidence ("unlabeled" without a label, "low" for an unknown one)."""
    value = verdict.get("has_target_disease")
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        value = value.strip().lower() == "true"
    if not isinstance(value, bool):
        value = None
    confidence = str(verdict.get("confidence") or "").strip().lower()
    if value is None or confidence not in CONFIDENCES:
        confidence = "unlabeled" if value is None else "low"
    return value, confidence


async def adjudicate(client: Any, model: str, record_text: str, prompt: str) -> dict[str, Any]:
    """One record's verdict, retried once when the answer is malformed."""
    messages = [
        {"role": "system", "content": f"You are a Clinical Adjudicator.\n\n{prompt}\n\n{_OUTPUT_FORMAT}"},
        {"role": "user", "content": f"Patient Record:\n{record_text}"},
    ]
    for attempt in range(2):
        response = await client.chat.completions.create(model=model, messages=messages, temperature=0.0)
        content = response.choices[0].message.content
        verdict = parse_verdict(content)
        if verdict is not None:
            return verdict
        if attempt == 0:
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": _RETRY}]
    return dict(FAILED_VERDICT)


async def adjudicate_member(
    client: Any, model: str, volume_uid: str, record_text: str, prompt: str, limit: asyncio.Semaphore
) -> dict[str, Any]:
    """``adjudicate()`` for one cohort member, at most ``limit`` at a time; the
    verdict carries the member's volume uid, which the model never sets."""
    async with limit:
        verdict = await adjudicate(client, model, record_text, prompt)
    verdict["volume_uid"] = volume_uid
    return verdict


__all__ = [
    "CONFIDENCES",
    "FAILED_VERDICT",
    "adjudicate",
    "adjudicate_member",
    "criteria_prompt",
    "normalized_label",
    "parse_verdict",
]
