import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid.uuid4())


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(Identity, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Identity, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Offer(Identity, Base):
    __tablename__ = "offers"
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    pricing: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[dict] = mapped_column(JSON, default=dict)


class ICP(Identity, Base):
    __tablename__ = "icps"
    name: Mapped[str] = mapped_column(String(160))
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("icps.id"))
    offer: Mapped[str] = mapped_column(String(160), default="Revue du pipeline commercial")
    criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    exclusions: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class CampaignGroup(Identity, Base):
    __tablename__ = "campaign_groups"
    name: Mapped[str] = mapped_column(String(180))
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    notes: Mapped[str] = mapped_column(Text, default="")


class Campaign(Identity, Base):
    __tablename__ = "campaigns"
    name: Mapped[str] = mapped_column(String(180))
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    group_id: Mapped[str | None] = mapped_column(ForeignKey("campaign_groups.id"), index=True)
    niche: Mapped[str] = mapped_column(String(160), default="")
    purpose: Mapped[str] = mapped_column(String(30), default="cold_outreach")
    icp_id: Mapped[str] = mapped_column(ForeignKey("icps.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    mode: Mapped[str] = mapped_column(String(20), default="simulation")
    transport: Mapped[str] = mapped_column(String(30), default="smtp")
    budget_cents: Mapped[int] = mapped_column(Integer, default=0)
    budget_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    timezone: Mapped[str] = mapped_column(String(60), default="Europe/Paris")
    start_hour: Mapped[int] = mapped_column(Integer, default=9)
    end_hour: Mapped[int] = mapped_column(Integer, default=18)
    weekdays: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4])
    sequence: Mapped[list] = mapped_column(JSON, default=list)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class Account(Identity, Base):
    __tablename__ = "accounts"
    name: Mapped[str] = mapped_column(String(220))
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    siren: Mapped[str | None] = mapped_column(String(9), unique=True)
    country: Mapped[str] = mapped_column(String(2), default="FR")
    industry: Mapped[str] = mapped_column(String(180), default="")
    employee_count: Mapped[int | None] = mapped_column(Integer)
    hubspot_status: Mapped[str] = mapped_column(String(20), default="unknown")
    lifecycle: Mapped[str] = mapped_column(String(30), default="prospect")
    outreach_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_reason: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class Contact(Identity, Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("account_id", "identity_key"),)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    email_sender_id: Mapped[str | None] = mapped_column(ForeignKey("sending_mailboxes.id"))
    identity_key: Mapped[str] = mapped_column(String(350))
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(180), default="")
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    email_status: Mapped[str] = mapped_column(String(30), default="unknown")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone: Mapped[str] = mapped_column(String(40), default="")
    phone_type: Mapped[str] = mapped_column(String(20), default="unknown")
    phone_status: Mapped[str] = mapped_column(String(30), default="unknown")
    linkedin_url: Mapped[str] = mapped_column(String(600), default="")
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    professional_relevance: Mapped[str] = mapped_column(Text, default="")
    sourcing_note: Mapped[str] = mapped_column(Text, default="")
    information_status: Mapped[str] = mapped_column(String(30), default="pending")
    qualification: Mapped[dict] = mapped_column(JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")


class ProspectProfile(Base):
    __tablename__ = "prospect_profiles"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action: Mapped[str] = mapped_column(
        Text, default="Qualifier l’entreprise et identifier un interlocuteur"
    )
    call_brief: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class Evidence(Identity, Base):
    __tablename__ = "evidence"
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[str] = mapped_column(String(20), default="unverified")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Identity, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_due", "status", "due_at"),)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(220))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    outcome: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True, default=uid)


class Message(Identity, Base):
    __tablename__ = "messages"
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    step: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    provider_id: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sender_id: Mapped[str | None] = mapped_column(ForeignKey("sending_mailboxes.id"))
    sender_email: Mapped[str] = mapped_column(String(320), default="")
    sender_domain: Mapped[str] = mapped_column(String(253), default="")
    sender_provider: Mapped[str] = mapped_column(String(253), default="")
    sender_version: Mapped[int | None] = mapped_column(Integer)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    purpose: Mapped[str] = mapped_column(String(20), default="sequence", server_default="sequence")
    recipient_email: Mapped[str] = mapped_column(String(320), default="", server_default="")
    in_reply_to: Mapped[str] = mapped_column(String(998), default="", server_default="")
    reference_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    sent_body: Mapped[str] = mapped_column(Text, default="", server_default="")
    reply_event_id: Mapped[str | None] = mapped_column(String(36))


class Event(Identity, Base):
    __tablename__ = "events"
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(60))
    channel: Mapped[str] = mapped_column(String(20), default="system")
    summary: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(60), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(300), unique=True)


class Suppression(Identity, Base):
    __tablename__ = "suppressions"
    __table_args__ = (UniqueConstraint("kind", "value", "scope"),)
    kind: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(String(350))
    scope: Mapped[str] = mapped_column(String(20), default="all")
    reason: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class Opportunity(Identity, Base):
    __tablename__ = "opportunities"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(220))
    stage: Mapped[str] = mapped_column(String(30), default="conversation")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    deposit_cents: Mapped[int] = mapped_column(Integer, default=0)
    meeting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meeting_held: Mapped[bool] = mapped_column(Boolean, default=False)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    next_action: Mapped[str] = mapped_column(Text, default="")
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_reason: Mapped[str] = mapped_column(Text, default="")


class Integration(Identity, Base):
    __tablename__ = "integrations"
    provider: Mapped[str] = mapped_column(String(30), unique=True)
    encrypted_config: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="configured")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unit_cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    monthly_limit_cents: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class SendingMailbox(Identity, Base):
    __tablename__ = "sending_mailboxes"
    from_email: Mapped[str] = mapped_column(String(320), unique=True)
    from_name: Mapped[str] = mapped_column(String(120), default="Lumelia")
    domain: Mapped[str] = mapped_column(String(253))
    provider_group: Mapped[str] = mapped_column(String(253))
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_config: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="configured")
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    warmup_status: Mapped[str] = mapped_column(String(30), default="not_started")
    readiness_note: Mapped[str] = mapped_column(Text, default="")
    warmup_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)
    total_daily_limit: Mapped[int] = mapped_column(Integer, default=70)
    external_daily_reserve: Mapped[int] = mapped_column(Integer, default=20)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=600)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collection_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    legacy_integration_id: Mapped[str | None] = mapped_column(ForeignKey("integrations.id"), unique=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class EmailLimit(Identity, Base):
    __tablename__ = "email_limits"
    __table_args__ = (UniqueConstraint("scope", "key"),)
    scope: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(253))
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class Job(Identity, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "run_at"),)
    kind: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(30), default="")
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_id: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(250), unique=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class Expense(Identity, Base):
    __tablename__ = "expenses"
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(50), default="manual")
    category: Mapped[str] = mapped_column(String(40), default="enrichment")
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    note: Mapped[str] = mapped_column(Text, default="")


class AppState(Base):
    __tablename__ = "app_state"
    id: Mapped[str] = mapped_column(String(30), primary_key=True, default="global")
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly_limit_cents: Mapped[int] = mapped_column(Integer, default=0)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    worker_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentClient(Identity, Base):
    __tablename__ = "agent_clients"
    name: Mapped[str] = mapped_column(String(120), default="Agent Lumelia")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(60), default="agent-cli")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentAction(Identity, Base):
    __tablename__ = "agent_actions"
    __table_args__ = (Index("ix_agent_actions_queue", "status", "next_attempt_at", "priority"),)
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(220))
    executor: Mapped[str] = mapped_column(String(20), default="agent")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    client_id: Mapped[str | None] = mapped_column(ForeignKey("agent_clients.id", ondelete="SET NULL"))
    lease_hash: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_url: Mapped[str] = mapped_column(String(700), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    human_reason: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_key: Mapped[str | None] = mapped_column(String(64))


class ActionNotification(Identity, Base):
    __tablename__ = "action_notifications"
    action_id: Mapped[str] = mapped_column(ForeignKey("agent_actions.id", ondelete="CASCADE"))
    dedupe_key: Mapped[str] = mapped_column(String(300), unique=True)
    title: Mapped[str] = mapped_column(String(220))
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
