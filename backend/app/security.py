import hashlib
import json
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import LoginSession, User, now

hasher = PasswordHasher()


def password_hash(password: str):
    if len(password) < 12 or len(password) > 200:
        raise HTTPException(422, "Le mot de passe doit contenir entre 12 et 200 caractères.")
    return hasher.hash(password)


def password_valid(password: str, hashed: str):
    try:
        return hasher.verify(hashed, password)
    except VerificationError:
        return False


def digest(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db: Session, user: User):
    token = secrets.token_urlsafe(48)
    session = LoginSession(
        user_id=user.id,
        token_hash=digest(token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now() + timedelta(hours=settings().session_hours),
    )
    db.add(session)
    db.flush()
    return token, session


def current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("lumelia_session", "")
    session = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == digest(token), LoginSession.expires_at > now())
    )
    if not session:
        raise HTTPException(401, "Connexion requise.")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Compte indisponible.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf_token):
            raise HTTPException(403, "Session expirée ou requête non autorisée. Rechargez la page.")
        # Serialize state-changing actions with the worker final eligibility check.
        from .domain import state

        state(db, lock=True)
    request.state.login_session = session
    return user


def admin(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Cette action nécessite un administrateur.")
    return user


def encrypt_config(config: dict):
    if not settings().encryption_key:
        raise HTTPException(503, "La clé de chiffrement du serveur n’est pas configurée.")
    return Fernet(settings().encryption_key.encode()).encrypt(json.dumps(config).encode()).decode()


def decrypt_config(value: str):
    return json.loads(Fernet(settings().encryption_key.encode()).decrypt(value.encode()))
