"""Encrypted PostgreSQL dump. Restore never targets an existing database."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url

from app.config import settings


def environment(database=None):
    url = make_url(settings().database_url)
    env = os.environ.copy()
    env.update(
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
        PGDATABASE=database or url.database,
    )
    return env


def backup(path):
    key = settings().encryption_key
    if not key:
        raise SystemExit("Configure LUMELIA_ENCRYPTION_KEY before backup.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        dump = Path(temp) / "database.dump"
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)],
            env=environment(),
            check=True,
            capture_output=True,
            timeout=120,
        )
        blob = Fernet(key.encode()).encrypt(dump.read_bytes())
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(blob)
    print("Encrypted backup created.")


def restore(path, database):
    if not database.startswith("lumelia_restore_"):
        raise SystemExit(
            "Restore into a new database named lumelia_restore_*; never overwrite the running database."
        )
    blob = Fernet(settings().encryption_key.encode()).decrypt(Path(path).read_bytes())
    env = environment("postgres")
    # createdb fails if the target already exists; no destructive fallback.
    subprocess.run(["createdb", database], env=env, check=True, capture_output=True)
    with tempfile.TemporaryDirectory() as temp:
        dump = Path(temp) / "database.dump"
        dump.write_bytes(blob)
        subprocess.run(
            ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--dbname", database, str(dump)],
            env=env,
            check=True,
            capture_output=True,
        )
    print("Restored to the new database; validate before switching the app.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("path")
    parser.add_argument("--database")
    args = parser.parse_args()
    if args.action == "backup":
        backup(args.path)
    elif not args.database:
        parser.error("--database is required for restore")
    else:
        restore(args.path, args.database)


if __name__ == "__main__":
    main()
