"""Persisted state for human phone calls and imported call recordings."""

from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Identity, now


class CallSession(Identity, Base):
    __tablename__ = "call_sessions"
    __table_args__ = (
        Index("ix_call_sessions_processing", "processing_status", "next_processing_at"),
        Index("ix_call_sessions_account_created", "account_id", "created_at"),
    )

    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source: Mapped[str] = mapped_column(String(20))  # browser | mobile | inbound | upload
    status: Mapped[str] = mapped_column(String(30), default="intent")
    destination: Mapped[str | None] = mapped_column(String(40))
    caller_id: Mapped[str] = mapped_column(String(40), default="+33199000010")
    external_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(80), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[str] = mapped_column(String(30), default="awaiting_recording")
    processing_error: Mapped[str] = mapped_column(Text, default="")
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_processing_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    report_version: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class CallRecording(Identity, Base):
    __tablename__ = "call_recordings"
    __table_args__ = (Index("ix_call_recordings_retention", "retention_until", "storage_status"),)

    call_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30), default="upload")
    provider_ref: Mapped[str | None] = mapped_column(String(100), unique=True)
    blob_ref: Mapped[str | None] = mapped_column(String(500), unique=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    byte_size: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer, default=1)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    storage_status: Mapped[str] = mapped_column(String(30), default="pending")
    information_confirmed: Mapped[bool] = mapped_column(default=False)
    information_proof: Mapped[str] = mapped_column(Text, default="")
    retention_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: now() + timedelta(days=30)
    )


class CallTranscript(Identity, Base):
    __tablename__ = "call_transcripts"
    __table_args__ = (UniqueConstraint("call_id", "version"),)

    call_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    provider: Mapped[str] = mapped_column(String(30), default="deepgram")
    model: Mapped[str] = mapped_column(String(60), default="nova-3")
    language: Mapped[str] = mapped_column(String(20), default="fr")
    channels: Mapped[int] = mapped_column(Integer, default=1)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    words: Mapped[list] = mapped_column(JSON, default=list)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class CallProviderEvent(Identity, Base):
    __tablename__ = "call_provider_events"

    call_id: Mapped[str | None] = mapped_column(
        ForeignKey("call_sessions.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    payload_checksum: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
