from sqlalchemy import Engine, create_engine


def build_engine(url: str) -> Engine:
    if not url.startswith("postgresql"):
        raise ValueError("PostgreSQL is required")
    return create_engine(url, pool_pre_ping=True)
