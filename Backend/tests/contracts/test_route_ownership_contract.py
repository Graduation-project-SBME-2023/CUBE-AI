"""Every session-scoped route of the Application API checks its owner.

A route is session-scoped when its path names a session or a row that belongs
to one. Such a route must depend on `current_user`, directly or through an
`owned_*` dependency, which answers 404 for a session of another user. The
scan reads the dependency tree FastAPI built, so a new route is covered the
moment it is registered, and a dependency dropped from one route fails here.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.contract

# Path parameters that name a session or a row of one.
SESSION_SCOPED_PARAMS = {
    "session_id", "run_id", "node_id", "change_id", "attempt_id", "checkpoint_id",
    "chat_id", "message_id", "cohort_id", "feature_set_id", "review_id", "model_result_id",
    "radiomics_id", "report_id", "segmentation_batch_id", "file_id", "event_id",
}

# Listings across sessions: they must return only the caller's rows.
CROSS_SESSION_LISTINGS = {
    ("GET", "/session-events/"),
    ("GET", "/session-events/filter"),
    ("GET", "/radiomics/"),
    ("GET", "/radiomics/search/"),
}

# Creates that name their session in the body.
BODY_SCOPED_CREATES = {
    ("POST", "/session-events/"),
    ("POST", "/literature-reviews/"),
    ("POST", "/reports/"),
    ("POST", "/files/"),
    ("POST", "/files/renders"),
}


def _api_routes(routes):
    from fastapi.routing import APIRoute

    for route in routes:
        # Newer FastAPI keeps an included router as one wrapper entry instead
        # of copying its routes into the app; walk into it.
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included.routes)
        elif isinstance(route, APIRoute):
            yield route


def _routes():
    from application_api.main import app

    for route in _api_routes(app.routes):
        for method in route.methods:
            yield method, route.path, route


def _calls(dependant) -> set:
    found = {dependant.call}
    for child in dependant.dependencies:
        found |= _calls(child)
    return found


def _must_check_owner(method: str, path: str) -> bool:
    key = (method, path)
    named = set(re.findall(r"{(\w+)}", path))
    return bool(named & SESSION_SCOPED_PARAMS) or key in CROSS_SESSION_LISTINGS | BODY_SCOPED_CREATES


def test_every_session_scoped_route_checks_its_owner():
    from application_api.api.dependencies import current_user

    unchecked = sorted(
        f"{method} {path}"
        for method, path, route in _routes()
        if _must_check_owner(method, path) and current_user not in _calls(route.dependant)
    )
    assert not unchecked, "session-scoped routes without an ownership check:\n" + "\n".join(unchecked)


def test_the_listed_routes_exist():
    """A renamed route must not silently drop out of the lists above."""
    registered = {(method, path) for method, path, _ in _routes()}
    missing = sorted(f"{m} {p}" for m, p in CROSS_SESSION_LISTINGS | BODY_SCOPED_CREATES
                     if (m, p) not in registered)
    assert not missing, missing


def test_a_dropped_dependency_is_caught():
    """The scan is not vacuous: a route stripped of its check is reported."""
    from fastapi.dependencies.utils import get_dependant

    from application_api.api.dependencies import current_user

    async def read_cohort(cohort_id: str):  # the route without its owned_cohort
        return cohort_id

    stripped = get_dependant(path="/cohorts/{cohort_id}", call=read_cohort)
    assert _must_check_owner("GET", "/cohorts/{cohort_id}")
    assert current_user not in _calls(stripped)
