from alembic import context

from app.core.config import Settings
from app.db.database import build_engine

engine = build_engine(Settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
