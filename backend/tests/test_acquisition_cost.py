from app import models as m
from app.db import SessionLocal


def test_acquisition_cost_counts_companies_once_and_keeps_lost_and_shared_costs(client, flow):
    initial = client.get("/api/dashboard").json()
    assert initial["customers_won"] == 0
    assert initial["cost_per_customer_cents"] is None
    with SessionLocal() as db:
        owner = db.query(m.User).first().id
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        other = m.Campaign(
            name="Autre cohorte", icp_id=campaign.icp_id, offer_id=campaign.offer_id, owner_id=owner
        )
        account = m.Account(name="Autre entreprise", domain="other.example.org")
        db.add_all([other, account])
        db.flush()
        other_id = other.id
        for title in ["Premier contrat", "Deuxième contrat, même entreprise"]:
            db.add(
                m.Opportunity(
                    account_id=flow["account"]["id"],
                    campaign_id=campaign.id,
                    owner_id=owner,
                    title=title,
                    stage="won",
                )
            )
        lost = m.Opportunity(
            account_id=account.id, campaign_id=other.id, owner_id=owner, title="Affaire perdue", stage="lost"
        )
        db.add(lost)
        db.add_all(
            [
                m.Expense(campaign_id=campaign.id, amount_cents=10000, status="spent", category="labor"),
                m.Expense(campaign_id=other.id, amount_cents=20000, status="spent", category="enrichment"),
                m.Expense(amount_cents=3000, status="estimated", category="software"),
                m.Expense(amount_cents=9000, status="reserved", category="enrichment"),
            ]
        )
        db.commit()
        lost_id = lost.id
    stats = client.get("/api/dashboard").json()
    assert stats["won"] == 2
    assert stats["customers_won"] == 1
    assert stats["cost_per_customer_cents"] == 33000
    assert stats["shared_cost_cents"] == 3000
    cohort = client.get("/api/dashboard", params={"campaign_id": flow["campaign"]["id"]}).json()
    assert cohort["customers_won"] == 1
    assert cohort["cost_per_customer_cents"] == 10000
    assert cohort["shared_cost_cents"] == 3000
    empty = client.get("/api/dashboard", params={"campaign_id": other_id}).json()
    assert empty["cost_cents"] == 20000
    assert empty["cost_per_customer_cents"] is None
    with SessionLocal() as db:
        db.get(m.Opportunity, lost_id).stage = "won"
        db.commit()
    final = client.get("/api/dashboard").json()
    assert final["customers_won"] == 2
    assert final["cost_per_customer_cents"] == 16500
