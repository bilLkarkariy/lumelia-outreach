from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from . import email_policy


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    password: str = Field(min_length=1, max_length=200)


class ICPCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    offer_id: str
    criteria: dict = Field(default_factory=dict)
    exclusions: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=10000)

    @field_validator("criteria")
    @classmethod
    def criteria_valid(cls, value):
        for key in ("min_employees", "max_employees"):
            if key in value and (type(value[key]) is not int or value[key] < 0):
                raise ValueError("L’effectif doit être un entier positif ou nul.")
        if value.get("max_employees", float("inf")) < value.get("min_employees", 0):
            raise ValueError("L’effectif maximum doit être supérieur ou égal au minimum.")
        if "crm" in value and not isinstance(value["crm"], str):
            raise ValueError("Le CRM doit être un nom.")
        return value


class SequenceStep(Input):
    channel: Literal["email", "phone", "linkedin"]
    day: int = Field(ge=0, le=180)
    subject: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def email_copy(self):
        if self.channel == "email":
            email_policy.validate(self.subject, self.body)
        return self


class CampaignGroupCreate(Input):
    name: str = Field(min_length=1, max_length=180)
    offer_id: str
    notes: str = Field(default="", max_length=10000)

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        if not value.strip():
            raise ValueError("Donnez un nom à la campagne principale.")
        return value.strip()


class CampaignCreate(Input):
    name: str = Field(min_length=1, max_length=180)
    offer_id: str
    purpose: Literal["cold_outreach", "network"] = "cold_outreach"
    icp_id: str | None = None
    group_id: str | None = None
    niche: str = Field(default="", max_length=160)
    target_profile: ICPCreate | None = None
    owner_id: str | None = None
    mode: Literal["simulation", "live"] = "simulation"
    transport: Literal["smtp"] = "smtp"
    budget_cents: int = Field(default=0, ge=0, le=10000000)
    daily_limit: int = Field(default=10, ge=1, le=5000)
    timezone: str = "Europe/Paris"
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=18, ge=1, le=24)
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    sequence: list[SequenceStep] = Field(default_factory=list, max_length=10)
    config: dict = Field(default_factory=dict)
    language: Literal["", "fr", "en", "de", "nl", "it"] = ""
    localized_footer: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def target_valid(self):
        if not self.icp_id and not self.target_profile:
            raise ValueError("Choisissez ou définissez un profil cible.")
        if self.icp_id and self.target_profile:
            raise ValueError("Choisissez un profil existant ou créez un profil dédié.")
        if self.group_id and not self.niche.strip():
            raise ValueError("Précisez la niche de cette sous-campagne.")
        self.niche = self.niche.strip()
        return self

    @model_validator(mode="after")
    def valid_window(self):
        if self.end_hour <= self.start_hour:
            raise ValueError("La fin de plage doit être après le début.")
        return self

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Fuseau horaire inconnu") from exc
        return value

    @field_validator("weekdays")
    @classmethod
    def weekdays_valid(cls, value):
        if not value or any(x not in range(7) for x in value):
            raise ValueError("Choisissez au moins un jour valide")
        return sorted(set(value))


class AccountCreate(Input):
    name: str = Field(min_length=1, max_length=220)
    domain: str = Field(min_length=3, max_length=300)
    siren: str | None = Field(default=None, pattern=r"^\d{9}$")
    country: str = Field(default="FR", pattern=r"^[A-Z]{2}$")
    industry: str = Field(default="", max_length=180)
    employee_count: int | None = Field(default=None, ge=0)
    hubspot_status: Literal["unknown", "signal", "confirmed", "absent"] = "unknown"
    lifecycle: Literal["prospect", "customer", "excluded"] = "prospect"
    notes: str = Field(default="", max_length=10000)


class ContactCreate(Input):
    account_id: str
    campaign_id: str | None = None
    owner_id: str | None = None
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    role: str = Field(default="", max_length=180)
    email: EmailStr | None = None
    email_status: Literal["unknown", "valid", "invalid", "risky"] = "unknown"
    phone: str = Field(default="", max_length=50)
    phone_type: Literal["unknown", "direct", "switchboard"] = "unknown"
    phone_status: Literal["unknown", "appropriate", "invalid"] = "unknown"
    linkedin_url: str = Field(default="", max_length=600)
    professional_relevance: str = Field(default="", max_length=2000)
    sourcing_note: str = Field(default="", max_length=2000)
    information_status: Literal["pending", "planned", "provided"] = "pending"
    notes: str = Field(default="", max_length=10000)
    source_url: str = Field(default="", max_length=1000)
    source_type: Literal["manual", "csv", "official_website", "provider", "conversation"] = "manual"


class ContactEdit(Input):
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=180)
    email: EmailStr | None = None
    email_status: Literal["unknown", "valid", "invalid", "risky"] | None = None
    phone: str | None = Field(default=None, max_length=50)
    phone_type: Literal["unknown", "direct", "switchboard"] | None = None
    phone_status: Literal["unknown", "appropriate", "invalid"] | None = None
    linkedin_url: str | None = Field(default=None, max_length=600)
    professional_relevance: str | None = Field(default=None, max_length=2000)
    sourcing_note: str | None = Field(default=None, max_length=2000)
    information_status: Literal["pending", "planned", "provided"] | None = None
    notes: str | None = Field(default=None, max_length=10000)
    campaign_id: str | None = None
    owner_id: str | None = None


class EvidenceCreate(Input):
    field: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=5000)
    source_url: str = Field(default="", max_length=1000)
    source_type: str = Field(min_length=1, max_length=60)
    confidence: Literal["unverified", "signal", "confirmed"] = "unverified"


class Batch(Input):
    contact_ids: list[str] = Field(min_length=1, max_length=100)


class TaskCreate(Input):
    contact_id: str
    channel: Literal["phone", "linkedin", "review"]
    title: str = Field(min_length=1, max_length=220)
    due_at: datetime
    owner_id: str | None = None
    notes: str = Field(default="", max_length=10000)


class TaskComplete(Input):
    outcome: Literal[
        "not_reached",
        "switchboard",
        "wrong_number",
        "wrong_person",
        "callback",
        "no_need",
        "stop",
        "meeting",
        "done",
    ]
    notes: str = Field(default="", max_length=10000)
    next_at: datetime | None = None


class ManualEvent(Input):
    kind: Literal["reply", "positive_reply", "out_of_office", "bounce", "stop", "meeting", "note"]
    channel: Literal["email", "phone", "linkedin"] = "email"
    summary: str = Field(default="", max_length=10000)
    external_id: str | None = Field(default=None, max_length=200)
    next_at: datetime | None = None


class SuppressionCreate(Input):
    kind: Literal["email", "phone", "domain", "contact"]
    value: str = Field(min_length=1, max_length=350)
    scope: Literal["all", "email", "phone", "linkedin"] = "all"
    reason: str = Field(min_length=1, max_length=1000)


class IntegrationSave(Input):
    config: dict
    unit_cost_cents: int = Field(default=0, ge=0, le=100000)
    monthly_limit_cents: int = Field(default=0, ge=0, le=10000000)


class EnrichBatch(Batch):
    provider: Literal["apollo", "fullenrich", "hunter"]
    fields: list[Literal["email", "phone"]] = ["email"]


class Discovery(Input):
    campaign_id: str
    provider: Literal["apollo"] = "apollo"
    kind: Literal["companies", "people"] = "companies"
    filters: dict = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=100)
    page: int = Field(default=1, ge=1, le=500)


class OpportunityCreate(Input):
    contact_id: str
    title: str = Field(min_length=1, max_length=220)
    amount_cents: int | None = Field(default=None, ge=0)


class OpportunityEdit(Input):
    stage: Literal["conversation", "meeting", "proposal", "won", "lost"] | None = None
    amount_cents: int | None = Field(default=None, ge=0)
    deposit_cents: int | None = Field(default=None, ge=0)
    meeting_at: datetime | None = None
    meeting_held: bool | None = None
    qualified: bool | None = None
    next_action: str | None = Field(default=None, max_length=2000)
    next_action_at: datetime | None = None
    lost_reason: str | None = Field(default=None, max_length=2000)


class StateEdit(Input):
    paused: bool | None = None
    monthly_limit_cents: int | None = Field(default=None, ge=0, le=10000000)
    daily_limit: int | None = Field(default=None, ge=1, le=10000)
    daily_limit_mode: Literal["manual", "mailboxes"] | None = None


class ExpenseCreate(Input):
    campaign_id: str | None = None
    category: Literal["enrichment", "software", "mailbox", "hosting", "phone", "labor", "commission", "other"]
    amount_cents: int = Field(ge=0, le=10000000)
    note: str = Field(min_length=1, max_length=1000)


class MailboxConfig(Input):
    host: str = Field(min_length=3, max_length=253)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1000)
    address: EmailStr
    folder: str = Field(default="INBOX", min_length=1, max_length=100)


class SMTPConfig(Input):
    host: str = Field(min_length=3, max_length=253)
    port: int = Field(default=465, ge=1, le=65535)
    security: Literal["ssl", "starttls"] = "ssl"
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1000)
    from_email: EmailStr
    from_name: str = Field(default="Lumelia", min_length=1, max_length=120)
    reply_to: EmailStr | None = None
    legal_footer: str = Field(min_length=20, max_length=3000)
    privacy_url: str = Field(min_length=10, max_length=1000)
    dns_verified: bool = False
    mailboxes: list[MailboxConfig] = Field(min_length=1, max_length=5)


class MessageEdit(Input):
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def email_copy(self):
        email_policy.validate(self.subject, self.body)
        return self


class Resume(Input):
    reason: str = Field(min_length=10, max_length=1000)


class Reconcile(Input):
    outcome: Literal["accepted", "not_sent", "received"]
    evidence: str = Field(min_length=15, max_length=3000)
    external_id: str | None = Field(default=None, max_length=300)


class Retention(Input):
    retention_until: datetime
    reason: str = Field(min_length=10, max_length=1000)


class EraseContact(Input):
    confirmation: str


class DiscoveryAccept(Input):
    index: int = Field(ge=0, le=99)
    company: str = Field(min_length=1, max_length=220)
    domain: str = Field(min_length=3, max_length=300)
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    role: str = Field(default="", max_length=180)
    linkedin_url: str = Field(default="", max_length=600)
    review_note: str = Field(min_length=10, max_length=2000)
