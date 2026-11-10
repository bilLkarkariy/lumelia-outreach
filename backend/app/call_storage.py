"""Private, encrypted-at-rest local storage for call audio."""

import hashlib
import importlib.util
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken


class StorageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredAudio:
    blob_ref: str
    checksum_sha256: str
    byte_size: int


@dataclass(frozen=True)
class S3Configuration:
    bucket: str
    prefix: str
    endpoint_url: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None


MAX_ENCRYPTED_BYTES = 150 * 1024 * 1024


def _s3_configuration(root: str) -> S3Configuration:
    parsed = urlsplit(root)
    bucket = parsed.netloc
    parts = [part for part in parsed.path.split("/") if part]
    endpoint = urlsplit(os.getenv("AWS_ENDPOINT_URL", ""))
    if (
        parsed.scheme != "s3"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,61}[A-Za-z0-9]", bucket)
        or any(part in {".", ".."} for part in parts)
        or any(not re.fullmatch(r"[A-Za-z0-9._=-]+", part) for part in parts)
    ):
        raise StorageUnavailable("La destination S3 privée est invalide.")
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise StorageUnavailable("Le point d’accès S3 doit être une URL HTTPS configurée côté serveur.")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    region = os.getenv("AWS_DEFAULT_REGION", "")
    if not access_key or not secret_key or not region:
        raise StorageUnavailable("Les identifiants et la région du stockage S3 sont incomplets.")
    return S3Configuration(
        bucket=bucket,
        prefix="/".join(parts),
        endpoint_url=endpoint.geturl().rstrip("/"),
        region=region,
        access_key_id=access_key,
        secret_access_key=secret_key,
        session_token=os.getenv("AWS_SESSION_TOKEN") or None,
    )


def _s3_client(config: S3Configuration):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise StorageUnavailable("Le pilote S3 du serveur n’est pas installé.") from exc
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        aws_session_token=config.session_token,
        config=Config(signature_version="s3v4"),
    )


def _object_key(config: S3Configuration, blob_ref: str) -> str:
    expected_prefix = config.prefix + "/" if config.prefix else ""
    if not re.fullmatch(re.escape(expected_prefix) + r"[a-f0-9-]{36}\.fernet", blob_ref):
        raise StorageUnavailable("Référence audio privée invalide.")
    return blob_ref


def _new_blob_ref(root: str, recording_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9-]{36}", recording_id):
        raise StorageUnavailable("Identifiant d’enregistrement invalide.")
    if root.startswith("s3://"):
        config = _s3_configuration(root)
        return f"{config.prefix}/{recording_id}.fernet" if config.prefix else f"{recording_id}.fernet"
    return f"{recording_id}.fernet"


def storage_available(root: str, encryption_key: str) -> bool:
    if not root or not encryption_key:
        return False
    try:
        Fernet(encryption_key.encode())
        if root.startswith("s3://"):
            _s3_configuration(root)
            return importlib.util.find_spec("boto3") is not None
        target = Path(root).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target.is_dir()
    except (OSError, StorageUnavailable, ValueError):
        return False


def safe_blob_path(root: str, blob_ref: str) -> Path:
    if not re.fullmatch(r"[a-f0-9-]{36}\.fernet", blob_ref):
        raise StorageUnavailable("Référence audio privée invalide.")
    base = Path(root).expanduser().resolve()
    path = (base / blob_ref).resolve()
    if path.parent != base:
        raise StorageUnavailable("Référence audio privée invalide.")
    return path


def store_audio(root: str, encryption_key: str, recording_id: str, content: bytes) -> StoredAudio:
    if not content:
        raise StorageUnavailable("Le fichier audio est vide.")
    if not storage_available(root, encryption_key):
        raise StorageUnavailable("Le stockage audio privé chiffré n’est pas configuré.")
    blob_ref = _new_blob_ref(root, recording_id)
    encrypted = Fernet(encryption_key.encode()).encrypt(content)
    if root.startswith("s3://"):
        config = _s3_configuration(root)
        try:
            _s3_client(config).put_object(
                Bucket=config.bucket,
                Key=_object_key(config, blob_ref),
                Body=encrypted,
                ContentType="application/octet-stream",
                Metadata={"plaintext-sha256": hashlib.sha256(content).hexdigest()},
            )
        except StorageUnavailable:
            raise
        except Exception as exc:
            raise StorageUnavailable("Impossible d’écrire dans le stockage audio S3 privé.") from exc
        return StoredAudio(blob_ref, hashlib.sha256(content).hexdigest(), len(content))
    path = safe_blob_path(root, blob_ref)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(encrypted)
        temporary.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise StorageUnavailable("Impossible d’écrire dans le stockage audio privé.") from exc
    return StoredAudio(blob_ref, hashlib.sha256(content).hexdigest(), len(content))


def read_audio(root: str, encryption_key: str, blob_ref: str) -> bytes:
    if not storage_available(root, encryption_key):
        raise StorageUnavailable("Le stockage audio privé chiffré n’est pas configuré.")
    try:
        if root.startswith("s3://"):
            config = _s3_configuration(root)
            response = _s3_client(config).get_object(Bucket=config.bucket, Key=_object_key(config, blob_ref))
            if int(response.get("ContentLength", 0)) > MAX_ENCRYPTED_BYTES:
                raise StorageUnavailable("L’objet audio privé dépasse la taille autorisée.")
            body = response["Body"]
            try:
                encrypted = body.read(MAX_ENCRYPTED_BYTES + 1)
            finally:
                body.close()
            if len(encrypted) > MAX_ENCRYPTED_BYTES:
                raise StorageUnavailable("L’objet audio privé dépasse la taille autorisée.")
        else:
            encrypted = safe_blob_path(root, blob_ref).read_bytes()
        return Fernet(encryption_key.encode()).decrypt(encrypted)
    except (OSError, InvalidToken) as exc:
        raise StorageUnavailable("L’audio privé est absent ou illisible.") from exc
    except StorageUnavailable:
        raise
    except Exception as exc:
        raise StorageUnavailable("L’audio privé S3 est absent ou illisible.") from exc


def delete_audio(root: str, blob_ref: str) -> None:
    if root.startswith("s3://"):
        config = _s3_configuration(root)
        try:
            _s3_client(config).delete_object(Bucket=config.bucket, Key=_object_key(config, blob_ref))
            return
        except StorageUnavailable:
            raise
        except Exception as exc:
            raise StorageUnavailable("Impossible de supprimer l’audio S3 privé.") from exc
    safe_blob_path(root, blob_ref).unlink(missing_ok=True)
