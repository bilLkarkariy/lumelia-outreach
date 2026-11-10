"""Persistent objectives, executable steps and campaign-specific qualification."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Identity, now


class CampaignQualification(Identity, Base):
    __tablename__ = "campaign_qualifications"
    __table_args__ = (UniqueConstraint("account_id", "campaign_id"),)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    score: Mapped[int] = mapped_column(Integer, default=0)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action: Mapped[str] = mapped_column(Text, default="Qualifier pour cette campagne")
    call_brief: Mapped[str] = mapped_column(Text, default="")


class Mission(Identity, Base):
    __tablename__ = "missions"
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(220))
    objective: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="prepare_prospects")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    target_count: Mapped[int] = mapped_column(Integer, default=10)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_key: Mapped[str] = mapped_column(String(250), unique=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class MissionStep(Identity, Base):
    __tablename__ = "mission_steps"
    __table_args__ = (Index("ix_mission_steps_due", "status", "due_at"),)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    action_id: Mapped[str | None] = mapped_column(ForeignKey("agent_actions.id", ondelete="SET NULL"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(220))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    executor: Mapped[str] = mapped_column(String(20), default="agent")
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class CallReport(Identity, Base):
    __tablename__ = "call_reports"
    __table_args__ = (UniqueConstraint("call_id", "version"),)
    call_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.id", ondelete="CASCADE"))
    transcript_id: Mapped[str] = mapped_column(ForeignKey("call_transcripts.id", ondelete="CASCADE"))
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20), default="agent")
    request_key: Mapped[str] = mapped_column(String(250), unique=True)
