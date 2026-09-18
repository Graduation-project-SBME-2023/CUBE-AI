from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Chat session/message routes and repositories moved from
# Backend/monailabel/{endpoints,crud}/ to Backend/application_api/{api/routes,repositories}/
# during the database-ownership refactor (see
# test_service_route_ownership.py:test_database_source_tree_is_owned_only_by_application_api,
# which asserts monailabel/crud no longer exists at all). This test targeted
# the old paths and raised FileNotFoundError after the refactor landed —
# see cubeai-docs/backend/database_audit.md D-34.
ROUTES_ROOT = BACKEND_ROOT / "application_api" / "api" / "routes"
REPOSITORIES_ROOT = BACKEND_ROOT / "application_api" / "repositories"


def test_chat_session_collection_is_bounded_and_does_not_eager_load_messages():
    endpoint_source = (ROUTES_ROOT / "chat_sessions.py").read_text(encoding="utf-8")
    crud_source = (REPOSITORIES_ROOT / "chat_sessions.py").read_text(encoding="utf-8")

    assert "limit: int = Query(50, ge=1, le=100)" in endpoint_source
    assert "skip: int = Query(0, ge=0)" in endpoint_source
    list_section = crud_source[crud_source.index("async def get_chat_sessions"):]
    assert "selectinload(models.ChatSession.messages)" not in list_section
    assert "noload(models.ChatSession.messages)" in list_section
    assert '.offset(skip).limit(limit)' in list_section
    assert 'session.__dict__["messages"] = []' in list_section


def test_chat_message_history_is_bounded():
    endpoint_source = (ROUTES_ROOT / "chat_messages.py").read_text(encoding="utf-8")
    crud_source = (REPOSITORIES_ROOT / "chat_messages.py").read_text(encoding="utf-8")

    assert "limit: int = Query(50, ge=1, le=100)" in endpoint_source
    assert ".offset(skip)" in crud_source
    assert ".limit(limit)" in crud_source


def test_new_chat_sessions_use_the_same_start_and_activity_timestamp():
    crud_source = (REPOSITORIES_ROOT / "chat_sessions.py").read_text(encoding="utf-8")
    create_section = crud_source[
        crud_source.index("async def create_chat_session"):crud_source.index("async def get_chat_session")
    ]

    assert "started_at = datetime.now(timezone.utc)" in create_section
    assert "started_at=started_at" in create_section
    assert "last_activity=started_at" in create_section
