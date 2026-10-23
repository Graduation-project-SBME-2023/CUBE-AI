"""``ask_user``: the domain forms (merged plan, Phase 9.3 item 3; D28).

The suspension itself is the engine's (a PreToolUse ``defer``, Phase 9.4);
these tests cover the tool's part of the contract: which agent may ask with
which form, the request the UI renders, and the answer read back from the
domain rows on resume. The scripted suspension on ``FakeAgentEngine`` follows
once the engine types of Phase 9.3 (``Suspension``, ``Resume``) are merged.
"""

from __future__ import annotations

import httpx
import pytest

from agent_system.toolkit.agents.spec import IMPLICIT_TOOLS
from agent_system.toolpacks.interaction import ask_user as module
from agent_system.toolpacks.interaction.ask_user import (
    FormRequestError,
    ask_user,
    form_request,
    forms_for,
)
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

SESSION = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
COHORT_ID = "44444444-4444-4444-8444-444444444444"
BATCH_ID = "55555555-5555-4555-8555-555555555555"
POPULATION = "study_population_planner_agent"
SEGMENTATION = "segmentation_agent"
COHORT_ARGS = {"form": "cohort_approval", "payload": {"cohort_id": COHORT_ID, "summary": "41 CT volumes."}}
REVIEW_ARGS = {"form": "segmentation_review", "payload": {"segmentation_batch_id": BATCH_ID, "summary": "40 masks."}}  # fmt: skip


def _ctx(agent_type: str = POPULATION):
    return make_context(session_id=SESSION, run_id=RUN, attempt_id="a1", agent_type=agent_type)


def _cohort(**fields):
    return {"cohort_id": COHORT_ID, "frozen_at": "2026-10-23T10:00:00Z", "frozen_by": "user"} | fields


def _batch(**fields):
    pairs = [{"review_decision": d} for d in ("accepted", "accepted", "modified", "rejected")]
    return {"segmentation_batch_id": BATCH_ID, "session_id": SESSION, "review_status": "submitted",
            "pairs": pairs} | fields  # fmt: skip


def _cohort_routes(api, cohort, rows=3):
    api.get(f"/cohorts/session/{SESSION}/population").mock(
        return_value=httpx.Response(404) if cohort is None else httpx.Response(200, json=cohort)
    )
    api.get(f"/cohorts/{COHORT_ID}/analysis-rows").respond(json=[{}] * rows)


# --- who asks with what


def test_each_form_has_one_owner():
    assert forms_for(POPULATION) == ("cohort_approval",)
    assert forms_for(SEGMENTATION) == ("segmentation_review",)
    assert forms_for("feature_extraction_agent") == ()
    assert forms_for(None) == ()


def test_ask_user_is_an_implicit_tool():
    """It is never listed in allowed_tools; the engine gives it to the owners."""
    assert module.ASK_USER in IMPLICIT_TOOLS


def test_the_request_the_ui_renders():
    request = form_request(COHORT_ARGS["form"], COHORT_ARGS["payload"], POPULATION)

    assert request.as_payload() == {
        "form": "cohort_approval", "cohort_id": COHORT_ID, "summary": "41 CT volumes.",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("form", "payload", "agent", "code"),
    [
        ("diagnosis", {}, POPULATION, "unknown_form"),
        ("segmentation_review", REVIEW_ARGS["payload"], POPULATION, "form_not_owned"),
        ("cohort_approval", {"cohort_id": "c1", "summary": "s"}, POPULATION, "bad_request"),
        ("cohort_approval", {"cohort_id": COHORT_ID}, POPULATION, "bad_request"),
        # Refs and a summary only: no patient data rides along.
        ("cohort_approval", {"cohort_id": COHORT_ID, "summary": "s", "patients": ["p1"]}, POPULATION, "bad_request"),
    ],
)  # fmt: skip
def test_a_bad_call_is_refused_before_it_could_suspend(form, payload, agent, code):
    with pytest.raises(FormRequestError) as caught:
        form_request(form, payload, agent)

    assert caught.value.code == code


def test_the_tool_refuses_another_agents_form(fake_domain_api):
    result = call_tool(ask_user, COHORT_ARGS, _ctx(SEGMENTATION))

    assert result["error_code"] == "form_not_owned"
    assert "AskUserQuestion" in result["error_message"]


# --- the cohort approval, read back on resume


def test_an_approved_cohort(fake_domain_api):
    _cohort_routes(fake_domain_api, _cohort())

    result = call_tool(ask_user, COHORT_ARGS, _ctx())

    assert result == {
        "status": "answered", "form": "cohort_approval", "decision": "approved",
        "cohort_id": COHORT_ID, "final_cohort_size": 3,
    }  # fmt: skip


def test_a_cohort_frozen_automatically_says_so(fake_domain_api):
    _cohort_routes(fake_domain_api, _cohort(frozen_by="auto"))

    assert call_tool(ask_user, COHORT_ARGS, _ctx())["decision"] == "frozen_automatically"


@pytest.mark.parametrize(
    ("cohort", "code"),
    [
        (_cohort(frozen_at=None, frozen_by=None), "not_answered"),
        (_cohort(cohort_id="66666666-6666-4666-8666-666666666666"), "wrong_cohort"),
        (None, "not_found"),
    ],
)
def test_no_answer_yet_is_not_an_answer(fake_domain_api, cohort, code):
    _cohort_routes(fake_domain_api, cohort)

    assert call_tool(ask_user, COHORT_ARGS, _ctx())["error_code"] == code


# --- the segmentation review, read back on resume


def test_a_submitted_review(fake_domain_api):
    fake_domain_api.get(f"/segmentation-batches/{BATCH_ID}").respond(json=_batch())

    result = call_tool(ask_user, REVIEW_ARGS, _ctx(SEGMENTATION))

    assert result["decision"] == "reviewed"
    assert result["decisions"] == {"accepted": 2, "modified": 1, "rejected": 1, "auto": 0}
    assert result["n_masks"] == 3


def test_an_automatically_accepted_batch(fake_domain_api):
    batch = _batch(review_status="auto_accepted", pairs=[{"review_decision": "auto"}] * 2)
    fake_domain_api.get(f"/segmentation-batches/{BATCH_ID}").respond(json=batch)

    result = call_tool(ask_user, REVIEW_ARGS, _ctx(SEGMENTATION))

    assert (result["decision"], result["n_masks"]) == ("accepted_automatically", 2)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, json=_batch(review_status="pending")), "not_answered"),
        (httpx.Response(200, json=_batch(session_id="another-session")), "not_found"),
        (httpx.Response(404), "not_found"),
        (httpx.Response(503), "upstream_error"),
    ],
)
def test_a_review_not_submitted_yet(fake_domain_api, response, code):
    fake_domain_api.get(f"/segmentation-batches/{BATCH_ID}").mock(return_value=response)

    assert call_tool(ask_user, REVIEW_ARGS, _ctx(SEGMENTATION))["error_code"] == code
