from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Chat session/message routes and repositories moved from
# Backend/monailabel/{endpoints,crud}/ to Backend/application_api/{api/routes,repositories}/
# during the database-ownership refactor (see
# test_service_route_ownership.py:test_database_source_tree_is_owned_only_by_application_api,
# which asserts monailabel/crud no longer exists at all). This test targeted
# the old paths and raised FileNotFoundError after the refactor landed —
# see cubeai-docs/backend/database_audit.md D-34.
#
# The session/chat split renamed `chat_sessions` to `sessions` and split
# `chats` out of it, so the bounded-collection contract now applies to the two
# new surfaces.
ROUTES_ROOT = BACKEND_ROOT / "application_api" / "api" / "routes"
REPOSITORIES_ROOT = BACKEND_ROOT / "application_api" / "repositories"


def test_session_collection_is_bounded_and_does_not_eager_load_children():
    endpoint_source = (ROUTES_ROOT / "sessions.py").read_text(encoding="utf-8")
    crud_source = (REPOSITORIES_ROOT / "sessions.py").read_text(encoding="utf-8")

    assert "limit: int = Query(50, ge=1, le=100)" in endpoint_source
    assert "skip: int = Query(0, ge=0)" in endpoint_source

    list_section = crud_source[crud_source.index("async def get_sessions"):]
    # A session list must not drag every chat, event and plan row with it.
    assert "selectinload(SessionModel.chats)" not in list_section
    assert "noload(SessionModel.chats)" in list_section
    assert "noload(SessionModel.events)" in list_section
    assert ".offset(skip)" in list_section and ".limit(limit)" in list_section


def test_chat_collection_is_bounded():
    endpoint_source = (ROUTES_ROOT / "chats.py").read_text(encoding="utf-8")
    crud_source = (REPOSITORIES_ROOT / "chats.py").read_text(encoding="utf-8")

    assert "limit: int = Query(100, ge=1, le=100)" in endpoint_source
    assert "skip: int = Query(0, ge=0)" in endpoint_source
    assert ".offset(skip)" in crud_source and ".limit(limit)" in crud_source


def test_chat_message_history_is_bounded():
    endpoint_source = (ROUTES_ROOT / "chat_messages.py").read_text(encoding="utf-8")
    crud_source = (REPOSITORIES_ROOT / "chat_messages.py").read_text(encoding="utf-8")

    assert "limit: int = Query(50, ge=1, le=100)" in endpoint_source
    assert ".offset(skip)" in crud_source
    assert ".limit(limit)" in crud_source


def test_new_sessions_use_one_application_timestamp():
    """created_at and updated_at come from the same instant."""
    crud_source = (REPOSITORIES_ROOT / "sessions.py").read_text(encoding="utf-8")
    create_section = crud_source[
        crud_source.index("async def create_session"):crud_source.index("async def get_session")
    ]

    assert "now = datetime.now(timezone.utc)" in create_section
    assert "created_at=now" in create_section
    assert "updated_at=now" in create_section


def test_new_sessions_create_their_plan_in_the_same_transaction():
    """A committed session with no plan is rejected by a deferred constraint trigger, so the pair must be one transaction."""
    crud_source = (REPOSITORIES_ROOT / "sessions.py").read_text(encoding="utf-8")
    create_section = crud_source[
        crud_source.index("async def create_session"):crud_source.index("async def get_session")
    ]

    assert "SessionGraph(session_id=session.session_id" in create_section
    # One commit for both rows.
    assert create_section.count("await db.commit()") == 1
