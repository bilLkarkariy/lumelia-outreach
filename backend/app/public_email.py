"""Traceable review of an email published by the recipient's organisation.

Publication and DNS prove provenance and a mail route, never mailbox delivery.
The provider's deliverability status remains unchanged.
"""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from . import models as m


def publication_review(db, contact, campaign):
    if not campaign or campaign.config.get("email_validation_policy") != "official_publication_and_mx":
        return None
    if not contact.email or contact.email_status == "invalid":
        return None
    now = m.now()
    event = db.scalar(
        select(m.Event)
        .where(
            m.Event.contact_id == contact.id,
            m.Event.kind == "email_publication_reviewed",
            m.Event.actor_id.is_not(None),
            m.Event.created_at >= now - timedelta(days=90),
            m.Event.created_at <= now,
        )
        .order_by(m.Event.created_at.desc())
        .limit(1)
    )
    if not event or event.data.get("email") != contact.email:
        return None
    review = event.data
    try:
        checked = datetime.fromisoformat(review["mx_checked_at"])
        if not checked.tzinfo or not now - timedelta(days=90) <= checked <= now:
            return None
        url = urlsplit(review["source_url"])
        domain = db.get(m.Account, contact.account_id).domain.removeprefix("www.").lower()
        host = (url.hostname or "").lower().removeprefix("www.")
        if url.scheme not in {"http", "https"} or not (host == domain or host.endswith("." + domain)):
            return None
        if review.get("email_domain") != contact.email.rsplit("@", 1)[-1].lower():
            return None
        hosts = review.get("mx_hosts")
        if (
            not isinstance(hosts, list)
            or not hosts
            or not all(isinstance(x, str) and x.strip(" .") for x in hosts)
        ):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    evidence = db.scalar(
        select(m.Evidence.id)
        .where(
            m.Evidence.contact_id == contact.id,
            m.Evidence.account_id == contact.account_id,
            m.Evidence.field == "email",
            m.Evidence.value == contact.email,
            m.Evidence.source_type == "official_website",
            m.Evidence.source_url == review["source_url"],
            m.Evidence.observed_at >= now - timedelta(days=90),
            m.Evidence.observed_at <= now,
        )
        .limit(1)
    )
    return review if evidence else None
