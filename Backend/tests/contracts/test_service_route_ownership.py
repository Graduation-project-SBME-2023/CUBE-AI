from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MONAI_SERVER = BACKEND_ROOT / "monailabel" / "server.py"
APPLICATION_SERVER = BACKEND_ROOT / "monailabel" / "control_server.py"


def test_monai_registers_only_heavy_processing_routers():
    source = MONAI_SERVER.read_text(encoding="utf-8")
    registered = {line.strip().rstrip(",") for line in source.splitlines() if line.strip().endswith(".router,")}
    required = {
        "create_model.router",
        "my_endpoints_datastore.router",
        "new_handle.router",
        "norm_align.router",
        "inference_jobs.router",
        "training_jobs.router",
        "voxtell_cache_inventory.router",
    }
    forbidden = {
        "patient.router",
        "study.router",
        "volume.router",
        "model.router",
        "segmentation.router",
        "radiomics.router",
        "chat_sessions.router",
        "chat_messages.router",
        "workflow.router",
        "sync.router",
        "populations.router",
    }

    assert required <= registered
    assert not forbidden.intersection(registered)
    assert "DATABASE_URL" not in source
    assert "all_seeders" not in source


def test_application_api_owns_database_workflow_and_sync_routes():
    source = APPLICATION_SERVER.read_text(encoding="utf-8")
    registered = {line.strip().rstrip(",") for line in source.splitlines() if line.strip().endswith(".router,")}
    required = {
        "patient.router",
        "study.router",
        "volume.router",
        "model.router",
        "segmentation.router",
        "radiomics.router",
        "chat_sessions.router",
        "chat_messages.router",
        "workflow.router",
        "sync.router",
    }

    assert required <= registered
    assert "training_jobs.router" not in registered
    assert 'title="CUBE.AI Application API"' in source


def test_application_service_url_never_falls_back_to_monai():
    source = (BACKEND_ROOT / "mcp" / "service_urls.py").read_text(encoding="utf-8")
    app_assignment = source[source.index("APP_API_URL"):source.index("# Compatibility aliases")]

    assert "APP_API_URL" in app_assignment
    assert "CONTROL_API_URL" in app_assignment
    assert "MONAI_URL" not in app_assignment
