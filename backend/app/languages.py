"""Explicit recipient language and versioned sequence language, without country inference."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import schemas as s
from .db import get_db
from .security import current_user

router = APIRouter(prefix="/api/contacts")
LANGUAGES = {"fr": "Français", "en": "Anglais", "de": "Allemand", "nl": "Néerlandais", "it": "Italien"}


class LanguageReview(s.Input):
    language: str = Field(pattern=r"^(fr|en|de|nl|it|)$")
    note: str = Field(min_length=10, max_length=2000)
    source_url: str = Field(default="", max_length=1000)


def reviewed(db, contact):
    row = db.scalar(
        select(m.Event)
        .where(m.Event.contact_id == contact.id, m.Event.kind == "language_reviewed")
        .order_by(m.Event.created_at.desc(), m.Event.id.desc())
        .limit(1)
    )
    if not row:
        return {"language": "", "note": "", "source_url": "", "reviewed_at": None}
    return {**row.data, "reviewed_at": row.created_at.isoformat()}


def reasons(db, contact, campaign):
    language = campaign.config.get("language", "")
    if not language:
        return []  # Existing sequences remain legacy until explicitly versioned.
    actual = reviewed(db, contact)["language"]
    result = []
    if actual != language:
        result.append("Langue du contact à confirmer pour la séquence en " + LANGUAGES[language].lower())
    if language != "fr" and len(campaign.config.get("localized_footer", "").strip()) < 20:
        result.append("Pied d’email dans la langue de la séquence à préparer")
    return result


@router.post("/{contact_id}/language")
def review(contact_id: str, data: LanguageReview, user=Depends(current_user), db: Session = Depends(get_db)):
    d.state(db, lock=True)
    contact = d.get(db, m.Contact, contact_id)
    d.validate_url(data.source_url)
    if data.language and data.language not in LANGUAGES:
        raise HTTPException(422, "Langue non prise en charge.")
    contact.approved = False
    if contact.status == "qualified":
        contact.status = "candidate"
    d.cancel_pending(db, contact, "Langue revue : relire et valider la séquence adaptée.")
    d.log(
        db,
        "language_reviewed",
        contact=contact,
        actor_id=user.id,
        summary="Langue : " + LANGUAGES.get(data.language, "À déterminer"),
        data=data.model_dump(),
    )
    db.commit()
    return reviewed(db, contact)
