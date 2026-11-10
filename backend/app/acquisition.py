"""Bounded Apify discovery on the server; remote starts are never replayed blindly."""

import hashlib
import json
import math
import re
from datetime import timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select

from . import domain as d
from . import models as m
from . import providers
from . import schemas as s
from .db import SessionLocal, get_db
from .security import admin, current_user, decrypt_config

router = APIRouter(prefix="/api/acquisition", tags=["acquisition"])
ACTOR = "compass~crawler-google-places"
BASE = "https://api.apify.com/v2"
KIND = "apify_discovery"
RUN_CAP_CENTS = 50  # USD, separate from the CRM's EUR expense ledger.
MONTH_CAP_CENTS = 500
ACTIVE = {"queued", "starting", "running", "uncertain"}


def request(config, method, path, **kwargs):
    return providers.request("apify", method, BASE + path, api_key=config["api_key"], **kwargs)


def usage(config):
    try:
        data = request(config, "GET", "/users/me/usage/monthly")["data"]
    except providers.ProviderError as exc:
        if exc.code == "HTTP 403":
            return {"used_usd": None, "unavailable": True}
        raise
    amount = data.get("totalUsageCreditsUsdAfterVolumeDiscount")
    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
        raise providers.ProviderError("apify", "Solde fournisseur indisponible")
    return {"used_usd": amount, "cycle": data.get("usageCycle", {})}


def check(config):
    request(config, "GET", "/acts/" + ACTOR + "/runs", params={"limit": 1})
    result = {"authenticated": True, "quota": usage(config)}
    try:
        result["account_bucket"] = request(config, "GET", "/users/me")["data"]["id"]
    except providers.ProviderError as exc:
        if exc.code != "HTTP 403":
            raise
    return result


def bind_previous_account(db, integration):
    """Keep the first account's consumed credit when replacing its credential."""
    if db.scalar(select(m.Job.id).where(m.Job.kind == KIND, m.Job.status.in_(ACTIVE))):
        raise HTTPException(409, "Terminez les collectes Apify en cours avant de changer de compte.")
    rows = list(
        db.scalars(
            select(m.Job).where(m.Job.kind == KIND, m.Job.payload["account_bucket"].as_string().is_(None))
        )
    )
    if not rows:
        return
    sample = next((row for row in rows if row.external_id), None)
    if sample:
        bucket = request(
            decrypt_config(integration.encrypted_config),
            "GET",
            "/actor-runs/" + quote(sample.external_id, safe=""),
        )["data"]["userId"]
        for row in rows:
            row.payload = {**row.payload, "account_bucket": bucket}


class Search(s.Input):
    request_id: UUID
    campaign_id: str
    search: str = Field(min_length=3, max_length=160)
    location: str = Field(min_length=3, max_length=160)
    limit: int = Field(default=100, ge=1, le=1250)
    start_now: bool = False
    max_charge_usd_cents: int = Field(default=RUN_CAP_CENTS, ge=RUN_CAP_CENTS, le=MONTH_CAP_CENTS)

    @model_validator(mode="after")
    def normal_limits(self):
        if not self.start_now and (self.limit > 100 or self.max_charge_usd_cents > RUN_CAP_CENTS):
            raise ValueError("Un lot ordinaire est limité à 100 établissements et 0,50 USD.")
        return self

    @field_validator("search", "location")
    @classmethod
    def clean(cls, value):
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("Précisez la recherche et la zone géographique.")
        return value


def spent(db):
    integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apify"))
    bucket = integration.state.get("account_bucket") if integration else None
    month = m.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    jobs = db.scalars(
        select(m.Job).where(
            m.Job.kind == KIND,
            (m.Job.payload["account_bucket"].as_string() == bucket) if bucket else True,
            (func.coalesce(m.Job.locked_at, m.Job.created_at) >= month) | m.Job.status.in_(ACTIVE),
        )
    )
    # Round the aggregate once so a sequence of small runs can use the remaining credit.
    return math.ceil(
        sum(
            row.result["charged_usd"] * 100
            if "charged_usd" in row.result
            else row.result.get("charged_usd_cents", row.payload.get("max_charge_usd_cents", RUN_CAP_CENTS))
            for row in jobs
        )
        - 1e-8
    )


def serial(row):
    return {
        "id": row.id,
        "status": row.status,
        "search": row.payload.get("search"),
        "location": row.payload.get("location"),
        "limit": row.payload.get("limit"),
        "at": row.created_at,
        "run_at": row.run_at,
        "error": row.error,
        "result": row.result,
        "run_url": "https://console.apify.com/actors/runs/" + row.external_id if row.external_id else None,
    }


@router.get("/status")
def status(limit: int = Query(10, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apify"))
    jobs = list(
        db.scalars(select(m.Job).where(m.Job.kind == KIND).order_by(m.Job.created_at.desc()).limit(limit))
    )
    return {
        "connected": bool(integration and integration.status == "authenticated"),
        "max_run_usd": RUN_CAP_CENTS / 100,
        "max_month_usd": MONTH_CAP_CENTS / 100,
        "reserved_or_spent_usd": spent(db) / 100,
        "jobs": [serial(row) for row in jobs],
    }


@router.post("/apify", status_code=202)
def start(data: Search, user=Depends(admin), db=Depends(get_db)):
    d.state(db, lock=True)
    key = "apify:" + str(data.request_id)
    payload = data.model_dump(mode="json", exclude={"request_id"})
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(m.Job).where(m.Job.idempotency_key == key))
    if existing:
        if existing.payload.get("fingerprint") != fingerprint:
            raise HTTPException(409, "Cette demande correspond déjà à une autre collecte.")
        return serial(existing)
    d.get(db, m.Campaign, data.campaign_id)
    integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apify"))
    if not integration or integration.status != "authenticated":
        raise HTTPException(409, "Connectez Apify dans Réglages avant de lancer une collecte.")
    if spent(db) + data.max_charge_usd_cents > MONTH_CAP_CENTS:
        raise HTTPException(409, "Plafond Apify mensuel atteint, en incluant les collectes réservées.")
    previous = db.scalar(select(m.Job).where(m.Job.kind == KIND).order_by(m.Job.run_at.desc()).limit(1))
    when = max(m.now(), previous.run_at + timedelta(hours=1)) if previous and not data.start_now else m.now()
    row = m.Job(
        kind=KIND,
        provider="apify",
        campaign_id=data.campaign_id,
        idempotency_key=key,
        run_at=when,
        payload={
            **payload,
            "fingerprint": fingerprint,
            "account_bucket": integration.state.get("account_bucket"),
        },
    )
    db.add(row)
    d.log(
        db,
        "acquisition_requested",
        actor_id=user.id,
        campaign_id=data.campaign_id,
        summary="Collecte d’entreprises programmée",
        data={"provider": "apify", "limit": data.limit},
    )
    db.commit()
    return serial(row)


def actor_input(payload):
    return {
        "searchStringsArray": [payload["search"]],
        "locationQuery": payload["location"],
        "maxCrawledPlacesPerSearch": payload["limit"],
        "language": "fr",
        "scrapeContacts": False,
        "scrapeDirectories": False,
        "scrapeSocialMediaProfiles": {
            "facebooks": False,
            "instagrams": False,
            "youtubes": False,
            "tiktoks": False,
            "twitters": False,
        },
        "maximumLeadsEnrichmentRecords": 0,
        "maxReviews": 0,
        "maxImages": 0,
        "scrapeReviewsPersonalData": False,
    }


def import_companies(db, job, items):
    counts = {
        "received": len(items),
        "created": 0,
        "existing": 0,
        "excluded": 0,
        "without_domain": 0,
        "closed": 0,
        "invalid": 0,
        "company_phones": 0,
        "duplicates": 0,
    }
    ids, seen = [], set()
    for item in items[: job.payload["limit"]]:
        if not isinstance(item, dict):
            counts["invalid"] += 1
            continue
        if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            counts["closed"] += 1
            continue
        try:
            domain = d.normalize_domain(str(item.get("website") or ""))
            if domain in {"facebook.com", "instagram.com", "linkedin.com", "google.com", "maps.google.com"}:
                raise ValueError("Not a company domain")
        except (HTTPException, ValueError):
            counts["without_domain"] += 1
            continue
        if domain in seen:
            counts["duplicates"] += 1
            continue
        seen.add(domain)
        name = str(item.get("title") or "").strip()[:220]
        country = str(item.get("countryCode") or "").upper()
        if not name or not re.fullmatch(r"[A-Z]{2}", country):
            counts["invalid"] += 1
            continue
        account = db.scalar(select(m.Account).where(m.Account.domain == domain))
        if account and (account.lifecycle != "prospect" or account.outreach_paused):
            counts["excluded"] += 1
            continue
        if account:
            counts["existing"] += 1
        else:
            account = m.Account(
                name=name,
                domain=domain,
                country=country,
                industry=str(item.get("categoryName") or "")[:180],
                notes="Entreprise repérée via Apify. Effectif, décideur et adéquation à la cible à vérifier.",
            )
            db.add(account)
            db.flush()
            counts["created"] += 1
        ids.append(account.id)
        # A listing's phone belongs to the company, never automatically to a named person.
        try:
            phone = d.normalize_phone(str(item.get("phoneUnformatted") or item.get("phone") or ""), country)
        except HTTPException:
            phone = ""
        source = str(item.get("url") or "")
        if not source.startswith("https://"):
            source = "https://console.apify.com/actors/runs/" + (job.external_id or "")
        existing_phone = db.scalar(
            select(m.Evidence.id).where(
                m.Evidence.account_id == account.id,
                m.Evidence.field == "company_phone",
                m.Evidence.value == phone,
            )
        )
        if phone:
            counts["company_phones"] += 1
            if not existing_phone:
                db.add(
                    m.Evidence(
                        account_id=account.id,
                        field="company_phone",
                        value=phone,
                        source_url=source[:2000],
                        source_type="apify",
                        confidence="unverified",
                    )
                )
        d.log(
            db,
            "company_discovered",
            campaign_id=job.campaign_id,
            source="apify",
            external_id="apify-company:" + job.id + ":" + account.id,
            summary="Entreprise collectée : " + name,
            data={"account_id": account.id, "job_id": job.id},
        )
    return {**counts, "account_ids": ids}


def tick():
    with SessionLocal() as db:
        d.state(db, lock=True)
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == KIND,
                m.Job.status == "starting",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status, stale.error = (
                "uncertain",
                "Démarrage interrompu : vérifier les runs Apify avant toute nouvelle collecte.",
            )
        row = db.scalar(
            select(m.Job)
            .where(m.Job.kind == KIND, m.Job.status.in_(["running", "queued"]), m.Job.run_at <= m.now())
            .order_by(m.Job.created_at)
            .limit(1)
        )
        if not row:
            db.commit()
            return 0
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apify"))
        if not integration or integration.status != "authenticated":
            row.error = "Connexion Apify indisponible."
            row.run_at = m.now() + timedelta(minutes=15)
            db.commit()
            return 0
        config = decrypt_config(integration.encrypted_config)
        job_id, starting = row.id, row.status == "queued"
        if starting:
            latest = db.scalar(
                select(m.Job.locked_at)
                .where(m.Job.kind == KIND, m.Job.locked_at.is_not(None), m.Job.id != row.id)
                .order_by(m.Job.locked_at.desc())
                .limit(1)
            )
            if latest and not row.payload.get("start_now") and latest + timedelta(hours=1) > m.now():
                row.run_at = latest + timedelta(hours=1)
                db.commit()
                return 0
            try:
                current = usage(config)
            except providers.ProviderError:
                row.error, row.run_at = (
                    "Solde Apify indisponible, collecte différée.",
                    m.now() + timedelta(minutes=15),
                )
                db.commit()
                return 0
            if (
                current["used_usd"] is not None
                and current["used_usd"] + row.payload.get("max_charge_usd_cents", RUN_CAP_CENTS) / 100
                > MONTH_CAP_CENTS / 100
            ):
                row.status, row.error = (
                    "cancelled",
                    "Crédit Apify disponible insuffisant dans le plafond de 5 USD.",
                )
                row.result = {"charged_usd_cents": 0}
                db.commit()
                return 0
            row.status, row.locked_at = "starting", m.now()
            row.attempts += 1
        else:
            # Claim this read/import for one minute, including across worker replicas.
            row.run_at = m.now() + timedelta(minutes=1)
        payload = row.payload
        remote_id = row.external_id
        db.commit()
    try:
        if starting:
            result = request(
                config,
                "POST",
                "/acts/" + ACTOR + "/runs",
                params={
                    "maxTotalChargeUsd": payload.get("max_charge_usd_cents", RUN_CAP_CENTS) / 100,
                    "timeout": 600,
                    "restartOnError": "false",
                    "memory": 1024,
                },
                payload=actor_input(payload),
            )["data"]
            with SessionLocal() as db:
                row = d.get(db, m.Job, job_id)
                row.external_id = str(result["id"])
                row.status, row.run_at, row.error = "running", m.now() + timedelta(seconds=30), ""
                db.commit()
            return 1
        result = request(config, "GET", "/actor-runs/" + quote(remote_id, safe=""))["data"]
        if result["status"] not in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
            return 0
        dataset = result.get("defaultDatasetId")
        items = (
            request(
                config,
                "GET",
                "/datasets/" + quote(dataset, safe="") + "/items",
                params={
                    "format": "json",
                    "clean": "true",
                    "limit": payload["limit"],
                    "fields": "title,website,phone,phoneUnformatted,countryCode,categoryName,url,placeId,permanentlyClosed,temporarilyClosed",
                },
            )
            if dataset
            else []
        )
        if not isinstance(items, list):
            raise providers.ProviderError("apify", "Résultat inattendu", uncertain=True)
        with SessionLocal() as db:
            d.state(db, lock=True)
            row = db.scalar(select(m.Job).where(m.Job.id == job_id).with_for_update())
            if row.status != "running":
                return 0
            outcome = import_companies(db, row, items)
            cost = result.get("usageTotalUsd")
            cost_fields = (
                {"charged_usd_cents": math.ceil(cost * 100), "charged_usd": cost}
                if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0
                else {}
            )
            row.result = {**outcome, **cost_fields, "remote_status": result["status"], "currency": "USD"}
            row.status = "completed" if result["status"] == "SUCCEEDED" else "partial"
            row.error = (
                ""
                if row.status == "completed"
                else "Collecte interrompue ; les résultats disponibles ont été conservés."
            )
            db.commit()
    except (providers.ProviderError, KeyError, TypeError) as exc:
        with SessionLocal() as db:
            row = d.get(db, m.Job, job_id)
            if starting:
                certain = isinstance(exc, providers.ProviderError) and not exc.uncertain
                row.status = "failed" if certain else "uncertain"
                if certain:
                    row.result = {"charged_usd_cents": 0}
            # GET failures can resume using the recorded run ID; POST failures cannot.
            row.error = str(exc) if isinstance(exc, providers.ProviderError) else "Réponse Apify incomplète."
            row.run_at = m.now() + timedelta(minutes=2)
            db.commit()
    return 1
