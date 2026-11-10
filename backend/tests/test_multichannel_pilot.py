from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.parametrize("outcome", ["not_reached", "callback", "meeting", "stop"])
def test_five_step_pilot_coordinates_call_outcomes(client, flow, outcome):
    original = flow["campaign"]
    sequence = [
        {"channel": "email", "day": day, "subject": "Recette", "body": "Suivi fictif"}
        if channel == "email"
        else {"channel": "phone", "day": day, "body": "Appel fictif"}
        for channel, day in [("email", 0), ("phone", 2), ("email", 5), ("phone", 8), ("email", 12)]
    ]
    version = client.post(
        f"/api/campaigns/{original['id']}/versions",
        json={
            "name": "Recette multicanal",
            "offer_id": original["offer_id"],
            "icp_id": original["icp_id"],
            "sequence": sequence,
        },
    )
    assert version.status_code == 201
    assert version.json()["status"] == "draft"
    key = flow["contact"]["id"]
    assert client.get(f"/api/contacts/{key}").json()["campaign_id"] == original["id"]
    assert client.patch(f"/api/contacts/{key}", json={"campaign_id": version.json()["id"]}).status_code == 200
    batch = {"contact_ids": [key]}
    for _ in range(2):
        assert client.post("/api/batches/prepare", json=batch).status_code == 200
    assert client.post("/api/batches/approve", json=batch).status_code == 200
    messages = client.get("/api/messages").json()
    calls = sorted(client.get("/api/tasks?channel=phone").json(), key=lambda row: row["due_at"])
    assert len(messages) == 3 and len(calls) == 2
    timeline = sorted(messages + calls, key=lambda row: row["due_at"])
    start = datetime.fromisoformat(timeline[0]["due_at"])
    for row, day in zip(timeline, [0, 2, 5, 8, 12], strict=True):
        assert abs((datetime.fromisoformat(row["due_at"]) - start).total_seconds() - day * 86400) < 2
    assert all(row["status"] == "draft" for row in calls)
    assert client.post(f"/api/campaigns/{version.json()['id']}/activate").status_code == 200
    result = {"outcome": outcome, "notes": "Conversation fictive de recette"}
    if outcome == "callback":
        result["next_at"] = (datetime.now(UTC) + timedelta(days=4)).isoformat()
    assert client.post(f"/api/tasks/{calls[0]['id']}/complete", json=result).status_code == 200
    # Repeating a recorded call must never create a second callback or restart a sequence.
    assert client.post(f"/api/tasks/{calls[0]['id']}/complete", json=result).status_code == 409
    refreshed = client.get("/api/messages").json()
    remaining = next(
        row for row in client.get("/api/tasks?channel=phone").json() if row["id"] == calls[1]["id"]
    )
    if outcome == "not_reached":
        assert all(row["status"] == "draft" for row in refreshed)
        assert remaining["status"] == "pending"
    else:
        assert all(row["status"] == "cancelled" for row in refreshed)
        assert remaining["status"] == "cancelled"
    if outcome == "callback":
        reviews = [
            row
            for row in client.get("/api/tasks?channel=phone").json()
            if row["dedupe_key"].startswith("callback:")
        ]
        assert len(reviews) == 1 and reviews[0]["status"] == "pending"
        assert datetime.fromisoformat(reviews[0]["due_at"]) == datetime.fromisoformat(result["next_at"])
