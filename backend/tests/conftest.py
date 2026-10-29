import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core import db as db_module
from app.core.config import settings
from app.core.permissions import DEFAULT_ROLE_PERMISSIONS, PERMISSIONS
from app.main import app
from app.models import Base, Permission, Role, RolePermission


def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    # SQLite ignores every ondelete="..." in the schema unless each connection
    # turns this on itself — off by default for backward compatibility with
    # pre-3.6.19 SQLite. Without it every CASCADE/RESTRICT the models declare
    # (see app/models/*.py) is silently a no-op in tests while genuinely
    # enforced in Postgres, which is worse than not testing it: it looks
    # covered.
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture(autouse=True)
def _force_anthropic_provider(monkeypatch):
    """The agent tests monkeypatch the Anthropic SDK directly, so the suite
    must run against that provider regardless of a developer's local
    AI_PROVIDER in .env (e.g. groq)."""
    monkeypatch.setattr(settings, "ai_provider", "anthropic")


class _FakeArqPool:
    async def enqueue_job(self, *args, **kwargs):
        return None


@pytest_asyncio.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with maker() as session:
        permission_ids = {}
        for code, desc in PERMISSIONS.items():
            perm = Permission(id=uuid.uuid4(), code=code, description=desc)
            session.add(perm)
            permission_ids[code] = perm.id

        for role_name, perm_codes in DEFAULT_ROLE_PERMISSIONS.items():
            role = Role(id=uuid.uuid4(), organization_id=None, name=role_name, is_system=True)
            session.add(role)
            await session.flush()
            for code in perm_codes:
                session.add(RolePermission(role_id=role.id, permission_id=permission_ids[code]))
        await session.commit()

    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_maker, monkeypatch):
    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[db_module.get_db] = override_get_db

    async def fake_get_pool():
        return _FakeArqPool()

    monkeypatch.setattr("app.api.v1.webhooks.get_pool", fake_get_pool)

    async def fake_trigger_quote_sent_workflow(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.workflow_service.trigger_quote_sent_workflow", fake_trigger_quote_sent_workflow)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def db_session(session_maker):
    async with session_maker() as session:
        yield session
