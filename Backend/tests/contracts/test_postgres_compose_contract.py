from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_postgres_18_volume_mounts_versioned_data_parent():
    compose = (
        REPOSITORY_ROOT / "deploy" / "docker-compose.backend.gpu.yml"
    ).read_text(encoding="utf-8")

    assert "postgres-db:/var/lib/postgresql\n" in compose
    assert "postgres-db:/var/lib/postgresql/data" not in compose
