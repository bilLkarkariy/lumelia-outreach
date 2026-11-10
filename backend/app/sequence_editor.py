"""Preview and edit sequences without changing already prepared campaigns."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d
from . import email_policy
from . import models as m
from . import schemas as s
from .db import get_db
from .security import admin, current_user

router = APIRouter(prefix="/api/campaigns", tags=["sequence editor"])
VARIABLES = {"prenom", "nom", "societe", "role", "pertinence"}
TOKEN = re.compile(r"\{\{\s*([\w_]+)\s*\}\}")


def template_issues(template):
    issues = []
    unknown = set(TOKEN.findall(template)) - VARIABLES
    if unknown:
        issues.append("Variables inconnues : " + ", ".join(sorted(unknown)))
    remainder = TOKEN.sub("", template)
    if "{{" in remainder or "}}" in remainder:
        issues.append("Une variable est mal formée. Utilisez {{prenom}}, par exemple.")
    return issues


def sequence_issues(steps):
    issues = []
    if not steps:
        return ["Ajoutez au moins une étape."]
    previous = -1
    for i, step in enumerate(steps):
        problems = template_issues(
            step["body"] + "\n" + (step["subject"] if step["channel"] == "email" else "")
        )
        if not step["body"].strip():
            problems.append("Le contenu est vide.")
        if step["channel"] == "email":
            if not step["subject"].strip() or "\n" in step["subject"] or "\r" in step["subject"]:
                problems.append("L’objet de l’email est manquant ou invalide.")
            if email_policy.contains_price(step["subject"], step["body"]):
                problems.append(email_policy.REASON)
        if step["day"] < previous:
            problems.append("Le jour doit être supérieur ou égal à celui de l’étape précédente.")
        previous = step["day"]
        issues.extend(f"Étape {i + 1} : {problem}" for problem in problems)
    return issues


class PreviewStep(s.Input):
    channel: Literal["email", "phone", "linkedin"]
    day: int = Field(ge=0, le=180)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=10000)


class SequencePreview(s.Input):
    sequence: list[PreviewStep] = Field(max_length=10)
    contact_id: str | None = None


class SequenceSave(s.Input):
    name: str = Field(min_length=1, max_length=180)
    sequence: list[s.SequenceStep] = Field(min_length=1, max_length=10)
    language: Literal["fr", "en", "de", "nl", "it"]
    localized_footer: str = Field(default="", max_length=5000)
    expected_updated_at: datetime
    request_id: UUID

    @model_validator(mode="after")
    def valid_sequence(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Donnez un nom à la sous-campagne.")
        issues = sequence_issues([step.model_dump() for step in self.sequence])
        if issues:
            raise ValueError(" ".join(issues))
        if (
            self.language != "fr"
            and any(step.channel == "email" for step in self.sequence)
            and len(self.localized_footer.strip()) < 20
        ):
            raise ValueError("Complétez le pied d’email dans la langue de la séquence.")
        return self


def in_place(db, campaign):
    return campaign.status == "draft" and not any(
        db.scalar(select(model.id).where(model.campaign_id == campaign.id).limit(1))
        for model in [m.Contact, m.Message, m.Task]
    )


@router.get("/{campaign_id}/sequence")
def get_sequence(campaign_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.get(db, m.Campaign, campaign_id)
    return {"campaign": d.serialize(row), "edit_in_place": in_place(db, row), "variables": sorted(VARIABLES)}


@router.post("/{campaign_id}/sequence/preview")
def preview_sequence(
    campaign_id: str, data: SequencePreview, user=Depends(current_user), db: Session = Depends(get_db)
):
    d.get(db, m.Campaign, campaign_id)
    contact = d.get(db, m.Contact, data.contact_id) if data.contact_id else None
    account = d.get(db, m.Account, contact.account_id) if contact else None
    fields = (
        {
            "prenom": contact.first_name,
            "nom": contact.last_name,
            "societe": account.name,
            "role": contact.role,
            "pertinence": contact.professional_relevance,
        }
        if contact
        else {}
    )
    steps = [step.model_dump() for step in data.sequence]
    items = []
    for step in steps:
        missing = sorted(
            {
                key
                for key in TOKEN.findall(
                    step["body"] + "\n" + (step["subject"] if step["channel"] == "email" else "")
                )
                if not str(fields.get(key, "")).strip()
            }
        )

        def render(value):
            return TOKEN.sub(
                lambda match: (
                    str(fields.get(match.group(1), "")).strip() or "⟦" + match.group(1) + " à renseigner⟧"
                ),
                value,
            )

        rendered = {
            **step,
            "subject": render(step["subject"]) if step["channel"] == "email" else "",
            "body": render(step["body"]),
            "missing": missing,
        }
        rendered["issues"] = template_issues(step["body"] + "\n" + step["subject"])
        if step["channel"] == "email" and email_policy.contains_price(rendered["subject"], rendered["body"]):
            rendered["issues"].append(email_policy.REASON)
        items.append(rendered)
    return {
        "contact": d.serialize(contact),
        "account": d.serialize(account),
        "steps": items,
        "issues": sequence_issues(steps),
    }


@router.put("/{campaign_id}/sequence")
def save_sequence(campaign_id: str, data: SequenceSave, user=Depends(admin), db: Session = Depends(get_db)):
    old = d.get(db, m.Campaign, campaign_id)
    key = f"sequence-edit:{campaign_id}:{data.request_id}"
    previous = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if previous:
        return {
            "campaign": d.serialize(d.get(db, m.Campaign, previous.campaign_id)),
            "saved_as": previous.data["saved_as"],
            "duplicate": True,
        }
    if data.expected_updated_at.tzinfo is None or old.updated_at != data.expected_updated_at:
        raise HTTPException(409, "La sous-campagne a changé. Rechargez sa version avant d’enregistrer.")
    mode = "draft" if in_place(db, old) else "new_version"
    if mode == "draft":
        row = old
        config = dict(old.config)
    else:
        row = m.Campaign(
            name=data.name,
            offer_id=old.offer_id,
            group_id=old.group_id,
            niche=old.niche,
            purpose=old.purpose,
            icp_id=old.icp_id,
            owner_id=user.id,
            mode=old.mode,
            transport=old.transport,
            budget_cents=old.budget_cents,
            daily_limit=old.daily_limit,
            timezone=old.timezone,
            start_hour=old.start_hour,
            end_hour=old.end_hour,
            weekdays=list(old.weekdays),
        )
        config = {"parent_campaign_id": old.id, "sequence_version": old.config.get("sequence_version", 1) + 1}
        db.add(row)
    row.name = data.name
    row.sequence = [step.model_dump() for step in data.sequence]
    row.config = {**config, "language": data.language, "localized_footer": data.localized_footer.strip()}
    row.budget_approved = False
    row.approved_by = None
    row.approved_at = None
    db.flush()
    d.log(
        db,
        "sequence_edited",
        campaign_id=row.id,
        actor_id=user.id,
        summary="Séquence enregistrée en brouillon."
        if mode == "draft"
        else "Nouvelle version en brouillon ; contacts et messages existants conservés.",
        data={"saved_as": mode, "parent_campaign_id": old.id},
        external_id=key,
    )
    db.commit()
    return {"campaign": d.serialize(row), "saved_as": mode, "duplicate": False}
