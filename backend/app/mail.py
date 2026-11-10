"""SMTP transport and read-only IMAP collection. SMTP acceptance is not inbox delivery."""

import hashlib
import imaplib
import re
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, formataddr, getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from . import domain as d
from . import email_policy
from . import models as m
from .config import settings
from .providers import ProviderError


def unsubscribe_token(contact_id):
    return Fernet(settings().encryption_key.encode()).encrypt(("unsubscribe:" + contact_id).encode()).decode()


def unsubscribe_contact(token):
    try:
        value = Fernet(settings().encryption_key.encode()).decrypt(token.encode()).decode()
        if not value.startswith("unsubscribe:"):
            raise ValueError()
        return value.split(":", 1)[1]
    except (InvalidToken, ValueError, UnicodeError):
        return None


def campaign_config(config, campaign):
    language = campaign.config.get("language") or "fr"
    return {
        **config,
        "language": language,
        "legal_footer": campaign.config.get("localized_footer")
        or (config.get("legal_footer", "") if language == "fr" else ""),
    }


def content(message, contact, config):
    link = settings().public_url.rstrip("/") + "/api/unsubscribe/" + unsubscribe_token(contact.id)
    labels = {
        "fr": ("Données personnelles", "Ne plus être contacté"),
        "en": ("Privacy information", "Stop receiving messages"),
        "de": ("Datenschutzhinweise", "Keine weiteren Nachrichten erhalten"),
        "nl": ("Privacyinformatie", "Geen verdere berichten ontvangen"),
        "it": ("Informativa sulla privacy", "Non ricevere altri messaggi"),
    }
    privacy_label, stop_label = labels.get(config.get("language", "fr"), labels["fr"])
    body = (
        message.body
        + "\n\n—\n"
        + config["legal_footer"]
        + "\n"
        + privacy_label
        + " : "
        + config["privacy_url"]
        + "\n"
        + stop_label
        + " : "
        + link
    )
    return body, link


def connect_smtp(config):
    context = ssl.create_default_context()
    if config["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(config["host"], config["port"], timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(config["host"], config["port"], timeout=20)
        try:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        except Exception:
            smtp.close()
            raise
    try:
        smtp.login(config["username"], config["password"])
    except Exception:
        smtp.close()
        raise
    return smtp


def connect_imap(config):
    mailbox = imaplib.IMAP4_SSL(
        config["host"], config["port"], ssl_context=ssl.create_default_context(), timeout=20
    )
    try:
        mailbox.login(config["username"], config["password"])
        status, _ = mailbox.select(config["folder"], readonly=True)
        if status != "OK":
            raise ValueError("Mailbox unavailable")
    except Exception:
        mailbox.logout()
        raise
    return mailbox


def verify(config):
    phase = "SMTP"
    try:
        with connect_smtp(config) as smtp:
            smtp.noop()
        for item in config["mailboxes"]:
            phase = "IMAP"
            mailbox = connect_imap(item)
            mailbox.logout()
        return {"authenticated": True, "mailboxes": len(config["mailboxes"])}
    except (smtplib.SMTPAuthenticationError, imaplib.IMAP4.error):
        raise ProviderError("smtp", phase + " : accès refusé ou dossier indisponible") from None
    except (TimeoutError, ConnectionError, OSError):
        raise ProviderError("smtp", phase + " : connexion réseau indisponible ou expirée") from None
    except Exception:
        raise ProviderError("smtp", phase + " : vérification de connexion échouée") from None


def build_message(message, contact, config):
    body, link = content(message, contact, config)
    body = message.sent_body or body
    if email_policy.contains_price(message.subject, body, config["from_name"]):
        raise ProviderError("smtp", email_policy.REASON, uncertain=False)
    email = EmailMessage()
    email["From"] = formataddr((config["from_name"], config["from_email"]))
    email["To"] = message.recipient_email or contact.email
    email["Reply-To"] = config.get("reply_to") or config["from_email"]
    email["Subject"] = message.subject
    email["Date"] = format_datetime(m.now())
    email["Message-ID"] = message.provider_id
    if message.in_reply_to:
        email["In-Reply-To"] = message.in_reply_to
    if message.reference_ids:
        email["References"] = " ".join(message.reference_ids)
    email["List-Unsubscribe"] = "<" + link + ">"
    # No List-Unsubscribe-Post: DKIM coverage of the required headers is not established.
    email.set_content(body)
    return email


def send(message, contact, config):
    email = build_message(message, contact, config)
    try:
        smtp = connect_smtp(config)
    except Exception:
        raise ProviderError(
            "smtp", "Connection/authentication failed before transmission", uncertain=False
        ) from None
    try:
        refused = smtp.send_message(
            email, from_addr=config["from_email"], to_addrs=[message.recipient_email or contact.email]
        )
        if refused:
            raise ProviderError("smtp", "Recipient refused", uncertain=False)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise ProviderError("smtp", "Message explicitly rejected", uncertain=False) from None
    except ProviderError:
        raise
    except Exception:
        raise ProviderError(
            "smtp", "Transmission result unknown; reconcile before retry", uncertain=True
        ) from None
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()
    return {"accepted": True, "message_id": message.provider_id}


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def email_content(email):
    body, truncated = "", False
    part = email.get_body(preferencelist=("plain", "html"))
    if part:
        try:
            body = part.get_content()
            if part.get_content_type() == "text/html":
                parser = PlainHTML()
                parser.feed(body)
                body = "".join(parser.parts)
            truncated = len(body) > 100000
            body = body[:100000]
        except Exception:
            body = "Contenu non décodable ; consultez la boîte mail."
    attachments = [
        {"name": str(part.get_filename() or "Pièce jointe")[:300], "type": part.get_content_type()}
        for part in email.iter_attachments()
    ][:40]
    try:
        at = parsedate_to_datetime(str(email.get("Date", "")))
        received_at = at.isoformat() if at.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        received_at = None
    return {
        "subject": str(email.get("Subject", ""))[:500],
        "body": body,
        "truncated": truncated,
        "attachments": attachments,
        "from_email": next((address.lower() for _, address in getaddresses(email.get_all("From", []))), ""),
        "to": [address.lower() for _, address in getaddresses(email.get_all("To", []))],
        "message_id": next(iter(re.findall(r"<[^<>\s]+>", str(email.get("Message-ID", "")))), "")[:998],
        "date": received_at,
    }


def parse_reply(db, raw, external_id, mailbox_id=None, folder="INBOX"):
    email = BytesParser(policy=policy.default).parsebytes(raw)
    from .native_warmup import record_receipt

    if record_receipt(db, email, mailbox_id, folder, external_id):
        return
    references = re.findall(
        r"<[^<>\s]+>", str(email.get("In-Reply-To", "")) + " " + str(email.get("References", ""))
    )
    # Prefer the immediate parent over older References; preserve exact attribution.
    direct = re.findall(r"<[^<>\s]+>", str(email.get("In-Reply-To", "")))
    references = list(dict.fromkeys(direct + list(reversed(references))))
    dsn_recipients = set()
    if email.get_content_type() == "multipart/report" and email.get_param("report-type") == "delivery-status":
        for part in email.walk():
            if part.get_content_type() == "message/delivery-status":
                for block in part.get_payload():
                    recipient = str(block.get("Final-Recipient", "")).split(";")[-1].strip().lower()
                    if recipient:
                        dsn_recipients.add(recipient)
            # Original returned headers often carry the stable Message-ID, without top-level References.
            for header in ["Message-ID", "In-Reply-To", "References"]:
                references.extend(re.findall(r"<[^<>\s]+>", str(part.get(header, ""))))
    candidates = (
        list(db.scalars(select(m.Message).where(m.Message.provider_id.in_(references)))) if references else []
    )
    message = next(
        (
            item
            for ref in references
            for item in candidates
            if item.provider_id == ref and (not mailbox_id or item.sender_id == mailbox_id)
        ),
        None,
    )
    attribution = "reference" if message else "sender_fallback"
    sender = {address.lower() for _, address in getaddresses(email.get_all("From", []))} | dsn_recipients
    if not message:
        contact = db.scalar(select(m.Contact).where(m.Contact.email.in_(sender)))
        if contact:
            message = db.scalar(
                select(m.Message)
                .where(
                    m.Message.contact_id == contact.id,
                    m.Message.status.in_(["sent", "uncertain"]),
                    *([m.Message.sender_id == mailbox_id] if mailbox_id else []),
                )
                .order_by(m.Message.created_at.desc())
                .limit(1)
            )
    if not message:
        return False
    contact = db.get(m.Contact, message.contact_id)
    if not contact or (contact.email or "").lower() not in sender:
        return False
    incoming = email_content(email)
    dedupe = (
        "email-in:"
        + (message.sender_id or mailbox_id or "unknown")
        + ":"
        + hashlib.sha256(incoming["message_id"].encode()).hexdigest()
        if incoming["message_id"]
        else external_id
    )
    if db.scalar(select(m.Event.id).where(m.Event.external_id == dedupe)):
        return True
    kind = "reply"
    if email.get_content_type() == "multipart/report" and email.get_param("report-type") == "delivery-status":
        statuses = []
        for part in email.walk():
            if part.get_content_type() == "message/delivery-status":
                for block in part.get_payload():
                    statuses.append(str(block.get("Action", "")).lower())
        if "failed" in statuses:
            kind = "bounce"
        else:
            kind = "note"
    elif str(email.get("Auto-Submitted", "no")).lower() != "no":
        kind = "out_of_office"
    summary = (incoming["subject"] + "\n" + incoming["body"]).strip()[:5500]
    d.receive_event(
        db,
        contact,
        kind,
        "email",
        summary,
        external_id=dedupe,
        source="imap",
        data={
            "message_id": message.id,
            "sender_id": message.sender_id or mailbox_id,
            "email": incoming,
            "attribution": attribution,
            "imap_external_id": external_id,
        },
        campaign_id=message.campaign_id,
    )
    return True


def collect(db, integration, config):
    checkpoints = dict(integration.state.get("mailboxes", {}))
    caught_up = True
    for index, item in enumerate(config["mailboxes"]):
        mailbox = connect_imap(item)
        try:
            validity = mailbox.response("UIDVALIDITY")[1][0].decode()
            key = str(index) + ":" + validity
            after = int(checkpoints.get(key, 0))
            status, result = mailbox.uid("search", None, "UID", f"{after + 1}:*")
            if status != "OK":
                raise ProviderError("smtp", "IMAP search failed")
            ids = [int(x) for x in result[0].split() if int(x) > after]
            if len(ids) > 100:
                caught_up = False
            for uid in ids[:100]:
                status, parts = mailbox.uid("fetch", str(uid), "(RFC822.SIZE)")
                sizes = re.findall(rb"RFC822.SIZE (\d+)", b" ".join(p for p in parts if isinstance(p, bytes)))
                if status != "OK" or not sizes:
                    raise ProviderError("smtp", "IMAP size lookup failed")
                if int(sizes[0]) > 2_000_000:
                    # Fail closed instead of skipping a potentially important response.
                    raise ProviderError(
                        "smtp", "Large incoming message requires manual review before collection can continue"
                    )
                status, parts = mailbox.uid("fetch", str(uid), "(BODY.PEEK[])")
                if status != "OK":
                    raise ProviderError("smtp", "IMAP fetch failed")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw is None:
                    raise ProviderError("smtp", "IMAP message unavailable")
                parse_reply(
                    db,
                    raw,
                    f"imap:{integration.id}:{key}:{uid}",
                    integration.id if isinstance(integration, m.SendingMailbox) else None,
                    item["folder"],
                )
                checkpoints[key] = uid
        finally:
            mailbox.logout()
    integration.state = {**integration.state, "mailboxes": checkpoints, "collector_error": ""}
    if caught_up:
        integration.last_event_at = m.now()
    return caught_up
