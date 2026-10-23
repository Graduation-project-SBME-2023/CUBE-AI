"""The model settings: a LiteLLM alias per model profile (merged plan, Phase 9.2 item 5)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import client_settings
from client_config import ClientConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def stored(monkeypatch):
    """The config the routes read and write, kept in memory (never on disk)."""
    holder = {"config": ClientConfig()}
    monkeypatch.setattr(client_settings, "get_client_config", lambda: holder["config"])
    monkeypatch.setattr(client_settings, "set_client_config", lambda config: holder.update(config=config))
    monkeypatch.setattr(client_settings, "save_config_to_disk", lambda config: None)
    return holder


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(client_settings.router)
    return TestClient(app)


def _row(response, profile):
    return next(row for row in response.json()["profiles"] if row["profile"] == profile)


def test_the_profiles_list_every_profile_with_its_default_alias(client, stored):
    response = client.get("/client-config/model-profiles")

    assert response.status_code == 200
    main = _row(response, "main")
    assert main["alias"] == main["default_alias"] == "cubeai-gpt-oss"
    assert {"name": "cubeai-gpt-oss", "locality": "external"} in main["aliases"]


def test_a_chosen_alias_is_saved_and_listed(client, stored):
    response = client.post("/client-config", json={"model_aliases": {"main": "cubeai-gpt-oss"}})

    assert response.status_code == 200
    assert response.json()["config"]["model_aliases"] == {"main": "cubeai-gpt-oss"}
    assert stored["config"].model_aliases == {"main": "cubeai-gpt-oss"}
    assert _row(client.get("/client-config/model-profiles"), "main")["alias"] == "cubeai-gpt-oss"


def test_choices_merge_per_profile_and_an_empty_alias_clears_one(client, stored):
    client.post("/client-config", json={"model_aliases": {"main": "cubeai-gpt-oss"}})
    client.post("/client-config", json={"model_aliases": {"composer": "cubeai-gpt-oss"}})

    assert stored["config"].model_aliases == {"main": "cubeai-gpt-oss", "composer": "cubeai-gpt-oss"}
    client.post("/client-config", json={"model_aliases": {"main": ""}})
    assert stored["config"].model_aliases == {"composer": "cubeai-gpt-oss"}


@pytest.mark.parametrize(
    ("choice", "detail"),
    [
        ({"planner": "cubeai-gpt-oss"}, "unknown model profile"),
        ({"main": "gpt-9"}, "unknown model alias"),
    ],
)
def test_an_unknown_profile_or_alias_is_refused(client, stored, choice, detail):
    response = client.post("/client-config", json={"model_aliases": choice})

    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert stored["config"].model_aliases == {}


def test_the_legacy_provider_settings_still_save(client, stored):
    response = client.post("/client-config", json={"model": "gpt-oss:20b"})

    assert response.status_code == 200
    assert stored["config"].model == "gpt-oss:20b"
