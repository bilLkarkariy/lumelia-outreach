import httpx
import respx

from app import models as m
from app import warmup
from app.db import SessionLocal
from tests.test_senders import sender_payload

BASE = "https://api.lemlist.com/api"


def inventory(client):
    client.post("/api/integrations/lemlist", json={"config": {"api_key": "fictional-secret"}})
    respx.get(BASE + "/user/channels").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan": None,
                "email": {
                    "available": False,
                    "accounts": [
                        {"id": "box-1", "email": "team@example.org", "provider": "custom"},
                        {"id": "box-2", "email": "owner@primary.example.net", "provider": "custom"},
                        {"id": "box-3", "email": "test@corporate.example.net", "provider": "custom"},
                    ],
                },
            },
        )
    )
    result = client.post("/api/warmup/sync")
    assert result.status_code == 200
    return result


@respx.mock
def test_warmup_inventory_is_independent_of_credit_api_and_filters_protected_domains(client):
    result = inventory(client)
    assert [row["email"] for row in result.json()["accounts"]] == ["team@example.org"]
    assert "fictional-secret" not in result.text
    assert client.post("/api/warmup/box-3/start").status_code == 404
    assert (
        client.post("/api/senders", json=sender_payload("test@news.corporate.example.net")).status_code == 422
    )
    assert len(respx.calls) == 1


@respx.mock
def test_monitoring_runs_while_campaigns_are_paused_and_does_not_invent_activity(client):
    inventory(client)
    row = client.post("/api/senders", json=sender_payload()).json()
    route = respx.get(BASE + "/lemwarm/box-1/settings").mock(
        return_value=httpx.Response(
            200,
            json={
                "warmEmailMax": 25,
                "warmEmailRampup": 3,
                "deliverability": {
                    "score": 60,
                    "lastAt": "2026-03-16T13:17:37Z",
                    "secret_details": "discard",
                },
            },
        )
    )
    assert warmup.tick() == 1
    data = client.get("/api/warmup").json()["accounts"][0]
    assert data["active"] is None and data["score_at"].startswith("2026-03-16")
    assert "discard" not in str(data)
    assert warmup.tick() == 0 and route.call_count == 1
    sender = client.get("/api/senders/" + row["id"]).json()
    assert sender["external_daily_reserve"] == 25 and sender["daily_limit"] == 45
    assert sender["paused"] and sender["warmup_status"] == "warming"


@respx.mock
def test_warmup_start_intent_is_durable_and_uncertain_command_never_retried(client):
    inventory(client)

    def timeout(request):
        with SessionLocal() as db:
            data = warmup.snapshot(warmup.connection(db))
            assert data["accounts"][0]["command"]["status"] == "pending"
        raise httpx.ReadTimeout("secret remote detail", request=request)

    route = respx.post(BASE + "/lemwarm/box-1/start").mock(side_effect=timeout)
    result = client.post("/api/warmup/box-1/start")
    assert result.status_code == 200
    assert result.json()["accounts"][0]["command"]["status"] == "uncertain"
    assert "secret remote detail" not in result.text
    assert client.post("/api/warmup/box-1/start").status_code == 409
    assert route.call_count == 1
    respx.post(BASE + "/lemwarm/box-1/pause").mock(return_value=httpx.Response(200, json={"ok": True}))
    assert client.post("/api/warmup/box-1/pause").json()["accounts"][0]["active"] is False


@respx.mock
def test_failed_start_reports_provider_limit_without_pretending_warmup_is_running(client):
    inventory(client)
    respx.post(BASE + "/lemwarm/box-1/start").mock(return_value=httpx.Response(402, text="fictional-secret"))
    result = client.post("/api/warmup/box-1/start")
    account = result.json()["accounts"][0]
    assert account["command"]["status"] == "failed" and "402" in account["error"]
    assert account.get("active") is not True and "fictional-secret" not in result.text
    with SessionLocal() as db:
        assert db.query(m.Job).count() == 0


@respx.mock
def test_settings_only_send_documented_fields_and_background_monitor_never_starts(client):
    inventory(client)
    route = respx.patch(BASE + "/lemwarm/box-1/settings").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    result = client.patch("/api/warmup/box-1/settings", json={"max_daily": 30, "daily_increase": 2})
    assert result.status_code == 200 and result.json()["accounts"][0]["max_daily"] == 30
    assert b'"warmEmailMax":30' in route.calls[0].request.content
    assert (
        client.patch("/api/warmup/box-1/settings", json={"max_daily": 80, "daily_increase": 2}).status_code
        == 422
    )
    respx.get(BASE + "/lemwarm/box-1/settings").mock(
        return_value=httpx.Response(200, json={"active": True, "warmEmailMax": 30})
    )
    warmup.tick()
    assert not any(call.request.url.path.endswith("/start") for call in respx.calls)
