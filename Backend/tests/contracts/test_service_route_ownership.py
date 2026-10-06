from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MONAI_SERVER = BACKEND_ROOT / "monailabel" / "server.py"
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
APPLICATION_SERVER = APPLICATION_ROOT / "main.py"


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
        "inference_models.router",
        "segmentation_masks.router",
        "radiomics.router",
        "chat_messages.router",
        "workflow.router",
        "sync.router",
    }

    assert required <= registered
    assert "chat_sessions.router" not in registered
    # The Application API keeps the training *receipt* and delegates
    # the training itself to MONAI over HTTP; it never runs it.
    if "training_jobs.router" in registered:
        service = (APPLICATION_ROOT / "services" / "training.py").read_text(encoding="utf-8")
        assert "INFERENCE_API_URL" in service
        for heavy in ("import torch", "import monailabel", "from monailabel", "import nnunet"):
            assert heavy not in service
    assert 'title="CUBE.AI Application API"' in source


def test_database_source_tree_is_owned_only_by_application_api():
    monai_root = BACKEND_ROOT / "monailabel"
    forbidden_directories = ("core", "crud", "models", "schemas", "seeders")

    assert all(not (monai_root / name).exists() for name in forbidden_directories)
    assert (APPLICATION_ROOT / "db" / "base.py").is_file()
    assert (APPLICATION_ROOT / "db" / "session.py").is_file()
    assert (APPLICATION_ROOT / "db" / "models").is_dir()
    assert (APPLICATION_ROOT / "repositories").is_dir()
    assert (APPLICATION_ROOT / "migrations" / "versions").is_dir()
    assert (APPLICATION_ROOT / "alembic.ini").is_file()
    assert not (BACKEND_ROOT / "migrations").exists()
    assert not (BACKEND_ROOT / "alembic.ini").exists()

    monai_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in monai_root.rglob("*.py")
    )
    assert "DATABASE_URL" not in monai_source
    assert "sqlalchemy" not in monai_source.lower()
    assert "application_api" not in monai_source


def test_application_image_owns_alembic_and_database_dependencies():
    repository_root = BACKEND_ROOT.parent
    dockerfile = (repository_root / "deploy" / "Dockerfile.application-api").read_text(encoding="utf-8")
    compose_files = (
        repository_root / "deploy" / "docker-compose.backend.gpu.yml",
    )
    alembic_config = (APPLICATION_ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert "COPY Backend/application_api /app/application_api" in dockerfile
    assert "COPY Backend/monailabel" not in dockerfile
    assert "script_location = %(here)s/migrations" in alembic_config

    # The image migrates, then seeds, then serves -- each step only if the one
    # before it succeeded, so the API never serves a schema that failed to migrate.
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    migrate = command.index("alembic -c /app/application_api/alembic.ini upgrade head")
    seed = command.index("python -m application_api.seeders")
    serve = command.index("exec python -m application_api.start")
    assert migrate < seed < serve
    assert command.count("&&") == 2

    for compose_file in compose_files:
        compose = compose_file.read_text(encoding="utf-8")
        monai_start = compose.index("\n  monailabel:")
        monai_end = compose.index("\n  orthanc:", monai_start)
        monai_service = compose[monai_start:monai_end]

        assert "DATABASE_URL" not in monai_service


def test_application_service_url_never_falls_back_to_monai(monkeypatch):
    from agent_system.toolpacks.common import service_urls

    for name in ("APP_API_URL", "CONTROL_API_URL", "INFERENCE_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MONAI_URL", "http://monailabel:8000")

    assert service_urls.app_api_url() == service_urls.DEFAULT_APP_API_URL
    monkeypatch.setenv("CONTROL_API_URL", "http://control:8001")
    assert service_urls.app_api_url() == "http://control:8001"
    monkeypatch.setenv("APP_API_URL", "http://application-api:8001/")
    assert service_urls.app_api_url() == "http://application-api:8001"
