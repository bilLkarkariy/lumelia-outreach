"""Lemwarm control and automatic read-only monitoring, independent of campaign sends.

Only explicit admin commands mutate Lemwarm. Persist their intent first, never retry
an ambiguous command, and never infer an active warm-up or readiness from a score.
"""

from datetime import datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import providers, senders
from . import schemas as s
from .db import SessionLocal, get_db
from .security import admin, current_user, decrypt_config

router = APIRouter(prefix="/api/warmup")
BASE = "https://api.lemlist.com/api"


class WarmupSettings(s.Input):
    max_daily: int = Field(ge=1, le=40)
    daily_increase: int = Field(ge=1, le=40)


def connection(db, *, lock=False):
    query = select(m.Integration).where(m.Integration.provider == "lemlist")
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def call(row, method, path, payload=None):
    return providers.request(
        "lemlist",
        method,
        BASE + path,
        api_key=decrypt_config(row.encrypted_config)["api_key"],
        payload=payload,
    )


def snapshot(row):
    data = (row.state or {}).get("warmup", {}) if row else {}
    return {
        "configured": row is not None,
        **data,
        "accounts": [item for item in data.get("accounts", []) if not senders.protected(item["email"])],
    }


def save(row, data):
    row.state = {**(row.state or {}), "warmup": data}


def find_account(row, mailbox_id):
    for account in snapshot(row)["accounts"]:
        if account["id"] == mailbox_id:
            return dict(account)
    raise HTTPException(404, "Cette boîte n’est pas disponible pour le warm-up.")


def save_account(row, account):
    data = snapshot(row)
    data.pop("configured", None)
    data["accounts"] = [account if item["id"] == account["id"] else item for item in data["accounts"]]
    save(row, data)


def inventory(row):
    data = snapshot(row)
    data.pop("configured", None)
    data["last_inventory_attempt_at"] = m.now().isoformat()
    try:
        result = call(row, "GET", "/user/channels")
        channel = result.get("email", {})
        if not isinstance(channel, dict) or not isinstance(channel.get("accounts"), list):
            raise providers.ProviderError("lemlist", "Format des boîtes indisponible")
        previous = {(a["id"], a["email"]): a for a in data["accounts"]}
        accounts = []
        for item in channel["accounts"][:100]:
            address = senders.canonical(str(item.get("email", "")))
            identifier = str(item.get("id", ""))
            if not identifier or "@" not in address or senders.protected(address):
                continue
            accounts.append(
                {
                    **previous.get((identifier, address), {}),
                    "id": identifier,
                    "email": address,
                    "provider": str(item.get("provider", "")),
                }
            )
        data.update(accounts=accounts, error="", last_inventory_at=m.now().isoformat())
    except providers.ProviderError as exc:
        data["error"] = str(exc)
    save(row, data)


def due(value, seconds):
    try:
        return datetime.fromisoformat(value) < m.now() - timedelta(seconds=seconds)
    except (ValueError, TypeError):
        return True


def sync_account(db, row, account):
    account["last_attempt_at"] = m.now().isoformat()
    try:
        result = call(row, "GET", "/lemwarm/" + quote(account["id"], safe="") + "/settings")
        if not isinstance(result, dict):
            raise providers.ProviderError("lemlist", "Réglages de warm-up indisponibles")
        maximum = result.get("warmEmailMax", result.get("dailyLimit"))
        ramp = result.get("warmEmailRampup")
        deliverability = result.get("deliverability") or {}
        score = deliverability.get("score")
        account.update(
            active=result.get("active") if type(result.get("active")) is bool else None,
            active_at=result.get("activeAt") if isinstance(result.get("activeAt"), str) else None,
            max_daily=maximum if type(maximum) is int else None,
            daily_increase=ramp if type(ramp) is int else None,
            score=score if type(score) is int and 0 <= score <= 100 else None,
            score_at=deliverability.get("lastAt") if isinstance(deliverability.get("lastAt"), str) else None,
            last_synced_at=m.now().isoformat(),
            error="",
        )
        # An explicit activity flag can reconcile an interrupted start/pause.
        command = account.get("command", {})
        if command.get("status") in {"pending", "uncertain"}:
            expected = {"start": True, "pause": False}.get(command.get("action"))
            if expected is not None and account["active"] is expected:
                account["command"] = {**command, "status": "reconciled"}
        reserve_capacity(db, account)
    except providers.ProviderError as exc:
        account["error"] = str(exc)
    save_account(row, account)


def reserve_capacity(db, account):
    maximum = account.get("max_daily")
    if type(maximum) is not int or maximum < 0:
        return
    sender = db.scalar(select(m.SendingMailbox).where(m.SendingMailbox.from_email == account["email"]))
    if not sender:
        return
    # Do not release reserve on an ambiguous activity state or a remote pause:
    # messages already sent by Lemwarm still count in the rolling 24 h window.
    sender.external_daily_reserve = max(sender.external_daily_reserve, maximum)
    sender.state = {**sender.state, "warmup_reserve": maximum}
    remaining = sender.total_daily_limit - sender.external_daily_reserve
    if remaining <= 0:
        sender.paused = True
    else:
        sender.daily_limit = min(sender.daily_limit, remaining)


@router.get("")
def get_status(user=Depends(current_user), db: Session = Depends(get_db)):
    return snapshot(connection(db))


@router.post("/sync")
def sync_inventory(user=Depends(admin), db: Session = Depends(get_db)):
    row = connection(db, lock=True)
    if not row:
        raise HTTPException(409, "Ajoutez la clé Lemlist dans les connexions.")
    inventory(row)
    db.commit()
    return snapshot(row)


@router.post("/{mailbox_id}/sync")
def sync_one(mailbox_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = connection(db, lock=True)
    account = find_account(row, mailbox_id)
    sync_account(db, row, account)
    db.commit()
    return snapshot(row)


def command(db, user, mailbox_id, action, payload=None):
    row = connection(db, lock=True)
    if not row:
        raise HTTPException(409, "Ajoutez la clé Lemlist dans les connexions.")
    account = find_account(row, mailbox_id)
    previous = account.get("command", {})
    # Always permit an explicit stop, except while a request is actually in flight.
    if previous.get("status") == "pending" and not due(previous.get("at"), 180):
        raise HTTPException(409, "Une commande est en cours pour cette boîte.")
    if previous.get("status") in {"pending", "uncertain"} and action != "pause":
        raise HTTPException(
            409,
            "Résultat précédent incertain. Actualisez l’état ou mettez le warm-up en pause avant de réessayer.",
        )
    operation = {"id": str(uuid4()), "action": action, "status": "pending", "at": m.now().isoformat()}
    account["command"] = operation
    save_account(row, account)
    expected_key = row.encrypted_config
    db.commit()  # Persist before any remote mutation, including settings changes.
    row = connection(db, lock=True)
    account = find_account(row, mailbox_id)
    if row.encrypted_config != expected_key or account.get("command", {}).get("id") != operation["id"]:
        raise HTTPException(409, "Connexion modifiée avant exécution ; actualisez les boîtes.")
    try:
        path = "/lemwarm/" + quote(mailbox_id, safe="") + "/" + action
        result = call(row, "PATCH" if action == "settings" else "POST", path, payload)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise providers.ProviderError("lemlist", "Commande non confirmée", uncertain=True)
        operation["status"] = "acknowledged"
        account["error"] = ""
        if action == "settings":
            account.update(max_daily=payload["warmEmailMax"], daily_increase=payload["warmEmailRampup"])
            reserve_capacity(db, account)
        else:
            account["active"] = action == "start"
            account["activity_source"] = "command"
            if action == "start":
                reserve_capacity(db, account)
    except providers.ProviderError as exc:
        operation["status"] = "uncertain" if exc.uncertain else "failed"
        account["error"] = str(exc)
    account["command"] = operation
    save_account(row, account)
    d.log(
        db, "warmup_" + action, actor_id=user.id, source="lemwarm", summary=account["email"], data=operation
    )
    db.commit()
    return snapshot(row)


@router.patch("/{mailbox_id}/settings")
def update_settings(
    mailbox_id: str, data: WarmupSettings, user=Depends(admin), db: Session = Depends(get_db)
):
    return command(
        db,
        user,
        mailbox_id,
        "settings",
        {
            "warmEmailMax": data.max_daily,
            "warmEmailRampup": data.daily_increase,
        },
    )


@router.post("/{mailbox_id}/start")
def start(mailbox_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    return command(db, user, mailbox_id, "start")


@router.post("/{mailbox_id}/pause")
def pause(mailbox_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    return command(db, user, mailbox_id, "pause")


def tick():
    # One read per cycle; warm-up monitoring continues while campaigns are paused.
    with SessionLocal() as db:
        row = db.scalar(
            select(m.Integration).where(m.Integration.provider == "lemlist").with_for_update(skip_locked=True)
        )
        if not row:
            return 0
        data = snapshot(row)
        if due(data.get("last_inventory_attempt_at"), 900):
            inventory(row)
        else:
            accounts = sorted(data["accounts"], key=lambda x: x.get("last_attempt_at", ""))
            account = next((a for a in accounts if due(a.get("last_attempt_at"), 300)), None)
            if not account:
                return 0
            sync_account(db, row, dict(account))
        db.commit()
    return 1
