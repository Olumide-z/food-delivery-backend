import sys
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db
from app.core.limiter import limiter

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(autouse=True)
def mock_email_sending():
    """Mock background email sending functions so tests run fast without hitting SMTP rate limits."""
    with patch("app.auth.routes.send_verification_code_email", new_callable=AsyncMock) as mock_verify, \
         patch("app.auth.routes.send_reset_password_email", new_callable=AsyncMock) as mock_reset:
        yield {"verify": mock_verify, "reset": mock_reset}


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Disable slowapi rate limiting so repeated test calls never return 429."""
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture(autouse=True)
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=main_module.app), base_url="http://test"
    ) as ac:
        yield ac
    main_module.app.dependency_overrides.clear()
