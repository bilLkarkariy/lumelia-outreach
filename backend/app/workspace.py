"""Single internal workspace: no sign-up, email login or user management."""

from sqlalchemy import text

from . import models as m
from . import offers
from .config import settings


def initialize(db):
    cfg = settings()
    if not cfg.access_password_hash:
        raise RuntimeError("Configure LUMELIA_ACCESS_PASSWORD_HASH before starting the workspace.")
    db.execute(text("SELECT pg_advisory_xact_lock(9701431)"))
    identity = db.get(m.User, "workspace")
    if not identity:
        identity = m.User(
            id="workspace",
            email="workspace",
            name="Lumelia",
            role="admin",
            password_hash=cfg.access_password_hash,
        )
        db.add(identity)
    elif identity.password_hash != cfg.access_password_hash:
        from sqlalchemy import delete

        db.execute(delete(m.LoginSession))
        identity.password_hash = cfg.access_password_hash
    offers.seed(db)
