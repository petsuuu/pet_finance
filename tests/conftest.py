import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://pet:local_dev_only@localhost/pet_test")

from app.core.config import Settings  # noqa: E402
from app.db.database import build_engine  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def migrate() -> Iterator[None]:
    # DATABASE_URL must point to a disposable database.
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.downgrade(config, "base")


@pytest.fixture()
def client(migrate: None) -> Iterator[TestClient]:
    settings = Settings()
    engine = build_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(
            text("""TRUNCATE idempotency_requests, transactions,
            accounts, categories, audit_log CASCADE""")
        )
    with TestClient(create_app(settings), headers={"Authorization": "Bearer test-token"}) as c:
        yield c
    engine.dispose()
