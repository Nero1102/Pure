import argparse
import os
from pathlib import Path

from .models import Base
from .session import Database, sqlite_url_for_path


def default_database_url(root_path=None):
    root = Path(root_path or os.getcwd())
    return sqlite_url_for_path(root / ".pure" / "pure.db")


def init_database(database_url: str | None = None):
    db = Database(database_url or os.environ.get("PURE_DATABASE_URL") or default_database_url())
    Base.metadata.create_all(db.engine)
    return db


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize the Pure metadata database.")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    db = init_database(args.database_url)
    print(f"initialized database: {db.database_url}")


if __name__ == "__main__":
    main()

