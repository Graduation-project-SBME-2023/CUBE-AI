from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_research_playground_uses_direct_services_during_local_development():
    config = (
        REPOSITORY_ROOT
        / "Frontend"
        / "OHIF-Viewer"
        / "extensions"
        / "research-playground"
        / "src"
        / "config.ts"
    ).read_text(encoding="utf-8")

    assert "usesDirectServicePorts" in config
    assert ":8001`" in config
    assert ":8150`" in config
    assert ":8000`" in config
    assert "REACT_APP_AGENTS_URL" in config
    assert "REACT_APP_AGENTS_WS_URL" in config


def test_gpt_oss_is_the_frontend_model_fallback():
    settings = (
        REPOSITORY_ROOT
        / "Frontend"
        / "OHIF-Viewer"
        / "extensions"
        / "research-playground"
        / "src"
        / "components"
        / "PlaygroundCanvas"
        / "ClientSettingsPanel.tsx"
    ).read_text(encoding="utf-8")

    assert "gpt-oss:120b-cloud" in settings
    assert "deepseek-v3.1" not in settings


def test_application_api_allows_only_configured_development_origins():
    application_api = (
        REPOSITORY_ROOT / "Backend" / "application_api" / "main.py"
    ).read_text(encoding="utf-8")

    assert '"CORS_ORIGINS"' in application_api
    assert 'allow_origins=_cors_origins' in application_api
    assert 'allow_origins=["*"]' not in application_api
