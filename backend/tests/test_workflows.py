from app.worker import tick


def test_replacing_phone_requires_new_qualification_and_cancels_followups(client, flow):
    prepare(client, flow)
    key = flow["contact"]["id"]
    changed = client.patch(
        f"/api/contacts/{key}",
        json={"phone": "01 99 00 00 43", "phone_type": "direct", "phone_status": "appropriate"},
    )
    assert changed.status_code == 200
    assert changed.json()["phone"] == "+33199000043"
    assert changed.json()["phone_type"] == "unknown"
    assert changed.json()["phone_status"] == "unknown"
    assert changed.json()["approved"] is False
    assert all(row["status"] == "cancelled" for row in client.get("/api/messages").json())
    assert all(row["status"] == "cancelled" for row in client.get("/api/tasks?channel=phone").json())
    reviewed = client.patch(
        f"/api/contacts/{key}",
        json={
            "phone_type": "switchboard",
            "phone_status": "appropriate",
            "sourcing_note": "Standard confirmé — recette fictive",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["phone"] == "+33199000043"
    assert reviewed.json()["phone_type"] == "switchboard"
    assert reviewed.json()["phone_status"] == "appropriate"
    assert reviewed.json()["approved"] is False


def prepare(client, flow):
    body = {"contact_ids": [flow["contact"]["id"]]}
    assert client.post("/api/batches/prepare", json=body).status_code == 200
    assert client.post("/api/batches/approve", json=body).status_code == 200
    message = client.get("/api/messages", params={"contact_id": flow["contact"]["id"]}).json()[0]
    assert client.post(f"/api/messages/{message['id']}/approve").status_code == 200
    assert client.post(f"/api/campaigns/{flow['campaign']['id']}/activate").status_code == 200
    return message


def test_simulation_is_idempotent_and_not_counted_as_sent(client, flow):
    prepare(client, flow)
    assert client.patch("/api/state", json={"paused": False}).status_code == 200
    assert tick() == 1
    assert tick() == 0
    stats = client.get("/api/dashboard").json()
    assert stats["sent"] == 0
    assert stats["simulated"] == 1
    assert client.get("/api/messages").json()[0]["sent_at"] is None


def test_reply_cancels_all_company_followups(client, flow):
    prepare(client, flow)
    payload = {**flow["payload"], "email": "bob@example.com", "first_name": "Bob"}
    second = client.post("/api/contacts", json=payload).json()
    assert client.post("/api/batches/prepare", json={"contact_ids": [second["id"]]}).status_code == 200
    assert client.post("/api/batches/approve", json={"contact_ids": [second["id"]]}).status_code == 409
    result = client.post(
        f"/api/contacts/{flow['contact']['id']}/events",
        json={"kind": "reply", "summary": "Parlons-en", "external_id": "reply-1"},
    )
    assert result.status_code == 200
    assert all(x["status"] == "cancelled" for x in client.get("/api/messages").json())
    assert all(x["status"] == "cancelled" for x in client.get("/api/tasks?channel=phone").json())
    assert client.get(f"/api/contacts/{second['id']}").json()["account"]["outreach_paused"]
    repeated = client.post(
        f"/api/contacts/{flow['contact']['id']}/events", json={"kind": "reply", "external_id": "reply-1"}
    ).json()
    assert repeated["duplicate"]
    assert len(client.get("/api/tasks?channel=review").json()) == 1


def test_suppression_survives_contact_edit_and_blocks_approval(client, flow):
    prepare(client, flow)
    assert (
        client.post(
            "/api/suppressions",
            json={"kind": "email", "value": "ALICE@example.com", "reason": "Arrêt demandé"},
        ).status_code
        == 201
    )
    key = flow["contact"]["id"]
    assert client.patch(f"/api/contacts/{key}", json={"notes": "Mise à jour"}).status_code == 200
    assert client.post("/api/batches/approve", json={"contact_ids": [key]}).status_code == 409
    assert client.get("/api/jobs").json()[0]["status"] == "cancelled"


def test_deduplication_and_partial_csv_import(client, flow):
    duplicate = client.post("/api/contacts", json=flow["payload"]).json()
    assert duplicate["new"] is False
    assert duplicate["id"] == flow["contact"]["id"]
    content = (
        "company,domain,first_name,email\nExample,example.org,Jean,jean@example.org\nMissing,,Test,bad\n"
    )
    result = client.post("/api/import/csv", content=content).json()
    assert result["created"] == 1
    assert len(result["errors"]) == 1
    repeated = client.post("/api/import/csv", content=content).json()
    assert repeated["duplicates"] == 1


def test_single_password_access_no_accounts_and_csrf(client):
    token = client.headers.pop("X-CSRF-Token")
    assert client.patch("/api/state", json={"paused": False}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/users", json={}).status_code == 404
    assert client.post("/api/auth/setup", json={}).status_code == 404
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"password": "test-password-1234", "email": "a@example.com"}
        ).status_code
        == 422
    )
    result = client.post("/api/auth/login", json={"password": "test-password-1234"})
    assert result.status_code == 200
    assert "email" not in result.json()["user"]
    assert client.get("/api/auth/status").json()["setup_required"] is False


def test_missing_template_variable_rolls_back_whole_preparation(client, flow):
    key = flow["contact"]["id"]
    client.patch(f"/api/contacts/{key}", json={"first_name": ""})
    assert client.post("/api/batches/prepare", json={"contact_ids": [key]}).status_code == 422
    assert client.get("/api/messages").json() == []


def test_opportunity_stops_outreach_and_records_deposit(client, flow):
    prepare(client, flow)
    result = client.post(
        "/api/opportunities",
        json={"contact_id": flow["contact"]["id"], "title": "Revue du pipeline commercial"},
    )
    assert result.status_code == 201
    key = result.json()["id"]
    assert (
        client.patch(f"/api/opportunities/{key}", json={"stage": "won", "deposit_cents": 375000}).status_code
        == 200
    )
    stats = client.get("/api/dashboard").json()
    assert stats["won"] == 1 and stats["deposits_cents"] == 375000
    assert client.get("/api/accounts").json()[0]["lifecycle"] == "customer"


def test_budget_reservations_block_overspend(client, flow):
    import pytest
    from fastapi import HTTPException

    from app import domain as d
    from app import models as m
    from app.db import SessionLocal

    with SessionLocal() as db:
        state = d.state(db)
        state.monthly_limit_cents = 100
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        campaign.budget_cents = 100
        campaign.budget_approved = True
        provider = m.Integration(provider="hunter", encrypted_config="test-only", monthly_limit_cents=100)
        db.add(provider)
        first = m.Job(kind="enrich", idempotency_key="budget-1")
        db.add(first)
        db.flush()
        d.reserve_budget(db, campaign, provider, first, 60)
        db.commit()
        second = m.Job(kind="enrich", idempotency_key="budget-2")
        db.add(second)
        db.flush()
        with pytest.raises(HTTPException) as exc:
            d.reserve_budget(db, campaign, provider, second, 60)
        assert exc.value.status_code == 409
        db.rollback()
    assert client.get("/api/dashboard").json()["reserved_cents"] == 60


def test_global_pause_prevents_simulation(client, flow):
    prepare(client, flow)
    assert tick() == 0
    assert client.get("/api/messages").json()[0]["status"] == "queued"


def test_missing_phone_does_not_block_valid_email(client, flow):
    key = flow["contact"]["id"]
    assert client.patch(f"/api/contacts/{key}", json={"phone": ""}).status_code == 200
    prepare(client, flow)
    assert client.get("/api/messages").json()[0]["status"] == "queued"
    assert client.get("/api/tasks?channel=phone").json()[0]["status"] == "draft"


def test_null_contact_field_is_validation_error(client, flow):
    assert (
        client.patch("/api/contacts/" + flow["contact"]["id"], json={"first_name": None}).status_code == 422
    )


def test_resuming_overdue_sequence_preserves_spacing(client, flow):
    from datetime import timedelta

    from app import models as m
    from app.db import SessionLocal

    prepare(client, flow)
    key = flow["contact"]["id"]
    client.post(
        f"/contacts/{key}/events".replace("/contacts", "/api/contacts"),
        json={"kind": "reply", "summary": "À reprendre après revue"},
    )
    with SessionLocal() as db:
        message = db.query(m.Message).filter_by(contact_id=key).first()
        task = db.query(m.Task).filter_by(contact_id=key, channel="phone").first()
        message.due_at = m.now() - timedelta(days=10)
        task.due_at = message.due_at + timedelta(days=2)
        db.commit()
    assert (
        client.post(
            f"/api/contacts/{key}/resume", json={"reason": "Reprise explicitement convenue avec le contact."}
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        message = db.query(m.Message).filter_by(contact_id=key).first()
        task = db.query(m.Task).filter_by(contact_id=key, channel="phone").first()
        assert task.due_at - message.due_at == timedelta(days=2)
        assert message.status == task.status == "draft"
        assert message.due_at > m.now() - timedelta(seconds=5)


def test_expired_retention_blocks_approval_and_send(client, flow):
    from datetime import timedelta

    from app.models import now

    prepare(client, flow)
    key = flow["contact"]["id"]
    assert (
        client.post(
            f"/api/contacts/{key}/retention",
            json={
                "retention_until": (now() - timedelta(days=1)).isoformat(),
                "reason": "Échéance atteinte selon notre politique.",
            },
        ).status_code
        == 200
    )
    assert client.post("/api/batches/approve", json={"contact_ids": [key]}).status_code == 409
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"


def test_export_and_erasure_preserve_reimport_suppression(client, flow):
    prepare(client, flow)
    key = flow["contact"]["id"]
    export = client.get(f"/api/contacts/{key}/export")
    assert export.status_code == 200
    assert export.json()["contact"]["email"] == "alice@example.com"
    assert len(export.json()["messages"]) == 1
    assert client.post(f"/api/contacts/{key}/erase", json={"confirmation": "wrong"}).status_code == 422
    assert client.post(f"/api/contacts/{key}/erase", json={"confirmation": "EFFACER"}).status_code == 200
    assert client.get(f"/api/contacts/{key}").status_code == 404
    assert client.get("/api/messages").json() == []
    assert client.get("/api/tasks").json() == []
    recreated = client.post("/api/contacts", json=flow["payload"]).json()
    assert client.post("/api/batches/approve", json={"contact_ids": [recreated["id"]]}).status_code == 409


def test_new_sequence_version_does_not_change_existing_campaign(client, flow):
    original = flow["campaign"]
    response = client.post(
        "/api/campaigns/" + original["id"] + "/versions",
        json={
            "name": "Version 2",
            "icp_id": original["icp_id"],
            "offer_id": original["offer_id"],
            "sequence": [{"channel": "email", "day": 0, "subject": "Autre objet", "body": "Autre approche"}],
        },
    )
    assert response.status_code == 201
    assert response.json()["config"]["sequence_version"] == 2
    assert response.json()["status"] == "draft"
    campaigns = client.get("/api/campaigns").json()
    assert next(c for c in campaigns if c["id"] == original["id"])["sequence"] == original["sequence"]
    assert client.get("/api/contacts/" + flow["contact"]["id"]).json()["campaign_id"] == original["id"]


def test_customer_account_edit_cancels_prospecting(client, flow):
    prepare(client, flow)
    account = flow["account"]
    response = client.put(
        "/api/accounts/" + account["id"],
        json={"name": account["name"], "domain": account["domain"], "lifecycle": "customer"},
    )
    assert response.status_code == 200
    assert response.json()["outreach_paused"]
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"


def test_delayed_start_moves_remaining_sequence_and_queued_jobs_together(client, flow):
    from datetime import timedelta

    from app import domain as d
    from app import models as m
    from app.db import SessionLocal

    prepare(client, flow)
    with SessionLocal() as db:
        message = db.query(m.Message).filter_by(contact_id=flow["contact"]["id"]).first()
        task = db.query(m.Task).filter_by(contact_id=flow["contact"]["id"], channel="phone").first()
        task.due_at = message.due_at + timedelta(days=2)
        before = message.due_at
        d.shift_sequence(db, message.contact_id, message.campaign_id, timedelta(days=5))
        db.flush()
        assert message.due_at == before + timedelta(days=5)
        assert task.due_at == message.due_at + timedelta(days=2)
        assert db.query(m.Job).filter_by(message_id=message.id).first().run_at == message.due_at
