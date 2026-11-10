from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings
from .json_safety import dumps


class Base(DeclarativeBase):
    pass


engine = create_engine(settings().database_url, pool_pre_ping=True, json_serializer=dumps)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
