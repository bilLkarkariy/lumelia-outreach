from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_backup_requires_session_and_csrf_and_exports_encrypted_postgres(client):
    with TestClient(app) as anonymous:
        assert anonymous.post("/api/backups").status_code == 401
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/backups").status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    response = client.post("/api/backups")
    assert response.status_code == 200, response.text[:100]
    assert ".dump.fernet" in response.headers["content-disposition"]
    raw = Fernet(settings().encryption_key.encode()).decrypt(response.content)
    assert raw.startswith(b"PGDMP")
    assert not response.content.startswith(b"PGDMP")
    assert client.post("/api/backups").status_code == 429
