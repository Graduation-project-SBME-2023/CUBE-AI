"""``ask_user``: the two domain forms an execution agent asks the user with
(merged plan, Phase 9.3 item 3; D28).

A free question is the SDK's ``AskUserQuestion``; nothing of ours is built for
it. Only the forms whose answer lands in domain rows need a tool:

- ``cohort_approval`` (``study_population_planner_agent``): the doctor reviews
  the cohort in the Population view, may add or remove volumes, and approves
  it; the approval route freezes it (``frozen_by = 'user'``);
- ``segmentation_review`` (``segmentation_agent``): the radiologist reviews the
  masks; the review route records each pair's decision and submits the batch.

An agent asks only when the node instructions say the user wants it; otherwise
the cohort is frozen and the batch accepted automatically.

**How the asking works** (the engine's part, Phase 9.4). The tool is never
listed in an ``allowed_tools``: the engine gives it to the agents that own a
form (``forms_for``). Its PreToolUse hook checks the call with
``form_request()`` (a bad call is denied with the error's message) and returns
``defer`` with the request as the suspension's payload: the attempt is
suspended (T10, kind ``ask_user``, with the call's ``tool_use_id``) and no
process waits. The UI renders the form from that payload, and the user answers
through the form's own domain route. On resume the hook lets the call through,
and this handler runs once: it reads the answer from the domain rows, so a
resume after a restart loses nothing and asks nothing twice.

The handler itself never waits: called before the user has answered, it
returns ``not_answered``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .._toolkit import error_result, tool
from ..common import domain_api
from ..common.manifests import READ_ONLY

ASK_USER = "ask_user"
COHORT_APPROVAL = "cohort_approval"
SEGMENTATION_REVIEW = "segmentation_review"
Form = Literal["cohort_approval", "segmentation_review"]

# The agent that owns each form: only it may ask with it.
FORM_OWNERS: dict[str, str] = {
    COHORT_APPROVAL: "study_population_planner_agent",
    SEGMENTATION_REVIEW: "segmentation_agent",
}
# The pair decisions (segmentation_pairs.review_decision): a submitted review
# records the first three; an automatically accepted batch records "auto".
REVIEW_DECISIONS = ("accepted", "modified", "rejected", "auto")
MASK_DECISIONS = ("accepted", "modified", "auto")  # the pairs whose mask later steps use


class _Request(BaseModel):
    """What the UI renders: refs and a summary, never patient data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    summary: str = Field(min_length=1, max_length=2000)


class CohortApprovalRequest(_Request):
    cohort_id: UUID


class SegmentationReviewRequest(_Request):
    segmentation_batch_id: UUID


REQUEST_MODELS: dict[str, type[_Request]] = {
    COHORT_APPROVAL: CohortApprovalRequest,
    SEGMENTATION_REVIEW: SegmentationReviewRequest,
}


class FormRequest(BaseModel):
    """The suspension's payload (``Suspension.payload``): the form and its request."""

    model_config = ConfigDict(frozen=True)

    form: Form
    request: CohortApprovalRequest | SegmentationReviewRequest

    def as_payload(self) -> dict[str, Any]:
        return {"form": self.form, **self.request.model_dump(mode="json")}


class FormRequestError(ValueError):
    """A call to ``ask_user`` that cannot become a form; ``code`` is its error code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def forms_for(agent_type: str | None) -> tuple[str, ...]:
    """The forms ``agent_type`` may ask with; an agent with none gets no ``ask_user``."""
    return tuple(form for form, owner in FORM_OWNERS.items() if owner == agent_type)


def form_request(form: str, payload: Any, agent_type: str | None) -> FormRequest:
    """Check one call: a known form, asked by its owner, with a valid request.
    Raises ``FormRequestError``; the engine's hook calls it before it defers."""
    if form not in FORM_OWNERS:
        raise FormRequestError(
            "unknown_form", f"{form!r} is not a form; the forms are {sorted(FORM_OWNERS)}"
        )
    if FORM_OWNERS[form] != agent_type:
        raise FormRequestError(
            "form_not_owned",
            f"only {FORM_OWNERS[form]} asks with {form}; ask a free question with "
            "AskUserQuestion instead",
        )
    try:
        request = REQUEST_MODELS[form].model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'payload'}: {error['msg']}"
            for error in exc.errors()
        )
        raise FormRequestError(
            "bad_request", f"the {form} payload is not valid: {problems}"
        ) from None
    return FormRequest(form=form, request=request)


@tool(
    name=ASK_USER,
    visibility={"agent"},
    idempotent=True,
    data_scopes={"phi_read"},  # it reads the cohort's members and the batch's pairs
    timeout_s=60,
    **READ_ONLY,
)
async def ask_user(
    form: Annotated[
        Form,
        Field(
            description=(
                "cohort_approval (the population step) or segmentation_review "
                "(the segmentation step)."
            )
        ),
    ],
    payload: Annotated[
        dict[str, Any],
        Field(
            description=(
                "The ids and a short summary the user sees: {cohort_id, summary} for "
                "cohort_approval, {segmentation_batch_id, summary} for segmentation_review. "
                "Never patient data."
            )
        ),
    ],
) -> dict[str, Any]:
    """Ask the user to approve the cohort or to review the masks, with the form the UI shows.
    Use it only when the node instructions say the user wants to approve or review this step,
    and call it alone. The node waits for the user without you; the result is their answer."""
    try:
        request = form_request(form, payload, domain_api.agent_type())
    except FormRequestError as exc:
        return error_result(exc.code, str(exc))
    try:
        if isinstance(request.request, CohortApprovalRequest):
            return await _cohort_answer(request.request)
        return await _review_answer(request.request)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, f"the {form} answer could not be read")


async def _cohort_answer(request: CohortApprovalRequest) -> dict[str, Any]:
    cohort = await domain_api.get_population_cohort()
    if cohort is None:
        return error_result("not_found", "this session has no population cohort to approve")
    if str(cohort.get("cohort_id")) != str(request.cohort_id):
        return error_result(
            "wrong_cohort",
            f"this session's cohort is {cohort.get('cohort_id')}, not {request.cohort_id}",
        )
    if not cohort.get("frozen_at"):
        return error_result(
            "not_answered",
            "the doctor has not approved the cohort yet",
            cohort_id=str(request.cohort_id),
        )
    rows = await domain_api.cohort_analysis_rows(str(request.cohort_id))
    by_user = cohort.get("frozen_by") == "user"
    return {
        "status": "answered",
        "form": COHORT_APPROVAL,
        "decision": "approved" if by_user else "frozen_automatically",
        "cohort_id": str(request.cohort_id),
        "final_cohort_size": len(rows),
    }


async def _review_answer(request: SegmentationReviewRequest) -> dict[str, Any]:
    batch_id = str(request.segmentation_batch_id)
    batch = await domain_api.request(
        "GET", f"/segmentation-batches/{domain_api.segment(batch_id)}", allow_404=True
    )
    if batch is None or str(batch.get("session_id")) != domain_api.session_id():
        return error_result(
            "not_found", f"segmentation batch {batch_id} is not a batch of this session"
        )
    status = batch.get("review_status")
    if status not in ("submitted", "auto_accepted"):
        return error_result(
            "not_answered",
            f"the radiologist has not submitted the review yet (review status: {status})",
            segmentation_batch_id=batch_id,
        )
    decisions = [pair.get("review_decision") for pair in batch.get("pairs") or []]
    counts = {decision: decisions.count(decision) for decision in REVIEW_DECISIONS}
    return {
        "status": "answered",
        "form": SEGMENTATION_REVIEW,
        "decision": "reviewed" if status == "submitted" else "accepted_automatically",
        "segmentation_batch_id": batch_id,
        "review_status": status,
        "decisions": counts,
        "n_masks": sum(counts[decision] for decision in MASK_DECISIONS),
    }
