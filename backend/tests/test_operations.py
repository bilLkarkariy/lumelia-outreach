from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app import models as m
from app.db import SessionLocal

from .test_senders import healthy


def test_search_and_quality_use_full_data_and_verification_age(client, flow):
    result = client.get("/api/search?q=Entreprise").json()
    assert result["groups"][0]["total"] == 1 and result["groups"][1]["total"] == 1
    assert client.get("/api/search?q=%25_").json()["total"] == 0
    assert client.get("/api/search?q=x").json()["total"] == 0
    with SessionLocal() as db:
        contact = db.get(m.Contact, flow["contact"]["id"])
        contact.email_verified_at = m.now() - timedelta(days=100)
        contact.updated_at = m.now()
        for proof in db.scalars(select(m.Evidence)):
            proof.observed_at = m.now() - timedelta(days=200)
        db.commit()
    result = client.get("/api/data-quality?issue=email_review").json()
    assert result["total"] == 1 and result["items"][0]["url"].endswith("?tab=qualification")
    assert {"email_review", "phone_review", "stale_source"} <= {
        row["key"] for row in result["items"][0]["issues"]
    }
    assert client.get("/api/data-quality?resource=accounts").json()["total"] == 1
    assert client.get("/api/data-quality?issue=unknown").status_code == 422


def test_calendar_exclusive_end_timezone_reschedule_and_stale_revision(client, flow):
    start = m.now()
    end = start + timedelta(days=7)
    first = client.post(
        "/api/tasks",
        json={
            "contact_id": flow["contact"]["id"],
            "channel": "review",
            "title": "Rappel fictif",
            "due_at": (start + timedelta(hours=1)).isoformat(),
        },
    ).json()
    client.post(
        "/api/tasks",
        json={
            "contact_id": flow["contact"]["id"],
            "channel": "review",
            "title": "Hors période",
            "due_at": end.isoformat(),
        },
    )
    result = client.get("/api/calendar", params={"start": start.isoformat(), "end": end.isoformat()}).json()
    assert result["total"] == 1 and result["items"][0]["record"]["contact"]["fit"]
    payload = {
        "kind": "task",
        "at": (start + timedelta(days=3)).isoformat(),
        "expected_updated_at": first["updated_at"],
        "request_id": str(uuid4()),
    }
    assert client.patch("/api/calendar/" + first["id"], json=payload).status_code == 200
    assert client.patch("/api/calendar/" + first["id"], json=payload).status_code == 200
    assert (
        client.patch("/api/calendar/" + first["id"], json={**payload, "request_id": str(uuid4())}).status_code
        == 409
    )
    assert (
        client.get(
            "/api/calendar", params={"start": start.replace(tzinfo=None).isoformat(), "end": end.isoformat()}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/calendar",
            params={"start": start.isoformat(), "end": (start + timedelta(days=44)).isoformat()},
        ).status_code
        == 422
    )


def test_calendar_contains_meetings_and_next_actions_without_contact(client, flow):
    at = m.now() + timedelta(days=1)
    with SessionLocal() as db:
        db.add(
            m.Opportunity(
                account_id=flow["account"]["id"],
                campaign_id=flow["campaign"]["id"],
                owner_id=db.scalar(select(m.User.id)),
                title="Projet fictif",
                meeting_at=at,
                next_action_at=at + timedelta(hours=2),
                next_action="Préparer le rendez-vous",
            )
        )
        db.commit()
    result = client.get(
        "/api/calendar", params={"start": m.now().isoformat(), "end": (at + timedelta(days=1)).isoformat()}
    ).json()
    assert {row["kind"] for row in result["items"]} == {"meeting", "next_action"}
    assert all(row["contact"] is None for row in result["items"])
    assert client.get("/api/search?q=Projet").json()["groups"][3]["total"] == 1


def test_stats_exact_step_dedup_excludes_manual_reply_and_fallback(client, flow):
    with SessionLocal() as db:
        first = m.Message(
            contact_id=flow["contact"]["id"],
            campaign_id=flow["campaign"]["id"],
            subject="Fictif",
            body="Fictif",
            step=0,
            status="sent",
            dedupe_key="stats-sent",
            sent_at=m.now(),
        )
        manual = m.Message(
            contact_id=first.contact_id,
            campaign_id=first.campaign_id,
            subject="Réponse",
            body="Fictif",
            step=-1,
            purpose="reply",
            status="sent",
            dedupe_key="stats-manual",
        )
        db.add_all([first, manual])
        db.flush()
        for _ in range(3):
            db.add(
                m.Event(
                    contact_id=first.contact_id,
                    campaign_id=first.campaign_id,
                    kind="positive_reply",
                    data={"message_id": first.id, "attribution": "reference"},
                )
            )
        db.add(
            m.Event(
                contact_id=first.contact_id,
                campaign_id=first.campaign_id,
                kind="reply",
                data={"message_id": manual.id, "attribution": "reference"},
            )
        )
        db.add(
            m.Event(
                contact_id=first.contact_id,
                campaign_id=first.campaign_id,
                kind="reply",
                data={"message_id": first.id, "attribution": "sender_fallback"},
            )
        )
        db.commit()
    result = client.get("/api/sequence-stats").json()
    step = result["versions"][0]["steps"][0]
    assert step["sent"] == 1 and step["replies"] == 1 and step["reply_rate"] == 100
    assert result["unattributed_events"] == 1 and result["manual_reply_events"] == 1


def test_alerts_current_connection_errors_and_overdue_resolve(client, flow):
    with SessionLocal() as db:
        box = healthy(db)
        box.last_event_at = m.now() - timedelta(hours=1)
        db.add(
            m.Task(
                contact_id=flow["contact"]["id"],
                channel="review",
                title="Ancien rappel",
                due_at=m.now() - timedelta(days=10),
            )
        )
        db.add(
            m.Job(
                kind="reply",
                status="uncertain",
                error="Résultat incertain",
                idempotency_key="uncertain-reply",
            )
        )
        db.get(m.AppState, "global").worker_seen_at = m.now()
        db.commit()
    result = client.get("/api/alerts").json()
    assert any(row["id"] == "overdue" for row in result["items"])
    assert any(row["id"].startswith("collection:") for row in result["items"])
    assert len(client.get("/api/alerts?severity=critical").json()["items"]) >= 2
    with SessionLocal() as db:
        db.scalar(select(m.SendingMailbox)).last_event_at = m.now()
        db.scalar(select(m.Task)).status = "completed"
        db.scalar(select(m.Job)).status = "completed"
        db.commit()
    assert client.get("/api/alerts").json()["total"] == 0
