from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_db_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def sqlite_url_for_path(path):
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


class Database:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = create_db_engine(database_url)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, future=True)

    @contextmanager
    def session(self):
        db = self.session_factory()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

