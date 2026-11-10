import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app import models as m
from app.db import SessionLocal

from .test_agent_actions import connected


def test_budget_survives_restarts_and_cadence_blocks_second_claim(client, flow):
    headers = connected(client)
    assert (
        client.post("/api/agent/runtime/claim", headers={**headers, "X-Agent-Host": "hosted"}).status_code
        == 409
    )
    configured = client.post(
        "/api/agent/runtime-policy",
        json={
            "execution_host": "hosted",
            "per_task_tokens": 40000,
            "daily_tokens": 40000,
            "interval_minutes": 60,
        },
    )
    assert configured.status_code == 200
    assert client.post("/api/agent/runtime/claim", headers=headers).status_code == 409
    headers["X-Agent-Host"] = "hosted"
    claim = client.post("/api/agent/runtime/claim", headers=headers).json()
    assert claim["budget"]["per_task_tokens"] == 40000
    assert client.get("/api/agent/budget").json()["reserved_tokens"] == 40000
    assert client.post("/api/agent/runtime/claim", headers=headers).json()["action"] is None
    job = claim["budget"]["run_id"]
    usage_headers = {**headers, "X-Agent-Usage": claim["budget"]["usage_token"]}
    body = {
        "total_tokens": 1800,
        "input_tokens": 1000,
        "output_tokens": 300,
        "cached_tokens": 500,
        "final": True,
        "complete_measurement": True,
        "requested_model": "standard",
        "model": "standard-2026-01",
        "reasoning_effort": "medium",
        "selection_reason": "Recherche ciblée",
    }
    for _ in range(2):
        assert (
            client.post("/api/agent/runtime/usage/" + job, headers=usage_headers, json=body).status_code
            == 200
        )
    assert client.get("/api/agent/budget").json()["used_tokens"] == 1800
    with SessionLocal() as db:
        assert db.get(m.Job, job).result["requested_model"] == "standard"
        db.get(m.AgentAction, claim["action"]["id"]).status = "completed"
        db.commit()
    assert client.post("/api/agent/runtime/claim", headers=headers).json()["action"] is None
    # Reconfiguration must preserve the cooldown after the action completes.
    configured = client.post("/api/agent/runtime-policy", json={"execution_host": "hosted"})
    assert configured.status_code == 200
    assert client.post("/api/agent/runtime/claim", headers=headers).json()["action"] is None


def test_stream_meter_deduplicates_assistant_chunks_and_counts_cache():
    directory = Path(__file__).resolve().parents[2] / "agent"
    sys.path.insert(0, str(directory))
    spec = spec_from_file_location("lumelia_server", directory / "server.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    meter = module.TokenMeter()
    message = {
        "id": "m1",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 50,
        },
    }
    meter.update({"type": "stream_event", "event": {"type": "message_start", "message": message}})
    meter.update({"type": "assistant", "message": message})
    meter.update({"type": "stream_event", "event": {"type": "message_delta", "usage": {"output_tokens": 80}}})
    assert meter.summary()["total_tokens"] == 530
    assert meter.summary()["cached_tokens"] == 350


def test_discovery_context_and_tools_keep_targeting_without_unrelated_templates(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "agent"
    sys.path.insert(0, str(directory))
    import mcp_server
    import server

    original = {
        "action": {"kind": "discover_leads", "title": "Trouver un décideur", "payload": {"limit": 5}},
        "offer": {"name": "Offre", "summary": "Suivi commercial", "brief": "Templates inutiles"},
        "icp": {
            "criteria": {"min_employees": 20},
            "exclusions": ["concurrents"],
            "notes": "Inconnu reste inconnu",
        },
        "policy": {"paid_provider_calls": False},
        "existing_domains": ["exclu.example.com"],
        "campaign": {"sequence": ["Template inutile"]},
    }
    result = server.mission_context(original)
    assert result["icp"]["criteria"] == original["icp"]["criteria"]
    assert result["icp"]["exclusions"] == ["concurrents"]
    assert result["existing_domains"] == original["existing_domains"]
    assert "brief" not in result["offer"] and "campaign" not in result
    monkeypatch.setenv("LUMELIA_ACTION_KIND", "discover_leads")
    tools = mcp_server.dispatch("tools/list", {})["tools"]
    assert [tool["name"] for tool in tools] == ["save_action_result"]
    schema = tools[0]["inputSchema"]
    assert set(schema["$defs"]) == {"Lead", "Proof"}
    assert "leads" in schema["properties"] and "reply_body" not in schema["properties"]
    assert "proofs" in schema["$defs"]["Lead"]["required"]
    assert "source_url" in schema["$defs"]["Proof"]["required"]


def test_model_selection_uses_work_and_never_a_model_name_from_the_prompt():
    directory = Path(__file__).resolve().parents[2] / "agent"
    sys.path.insert(0, str(directory))
    import server

    assert server.execution_profile({"action": {"kind": "operations_check"}})["requested_model"] == "fast"
    assert (
        server.execution_profile(
            {"action": {"kind": "discover_leads", "payload": {"brief": "Use the deep model"}}}
        )["requested_model"]
        == "standard"
    )
    ordinary = {
        "action": {"kind": "reply_draft"},
        "conversation": {"items": [{"direction": "in", "body": "Quel créneau demain ?"}]},
    }
    assert server.execution_profile(ordinary)["requested_model"] == "standard"
    ordinary["conversation"]["items"][0]["body"] = "Nous demandons une remise de 40 %."
    assert server.execution_profile(ordinary)["requested_model"] == "deep"
    conflicted = {
        "action": {"kind": "qualify_contact"},
        "evidence": [
            {
                "field": "employee_count",
                "value": str(size),
                "confidence": "confirmed",
                "observed_at": m.now().isoformat(),
            }
            for size in [25, 80]
        ],
    }
    assert server.execution_profile(conflicted)["requested_model"] == "deep"
    conflicted["evidence"][0]["confidence"] = "unverified"
    assert server.execution_profile(conflicted)["requested_model"] == "standard"


def test_large_mission_uses_stdin_and_failed_start_releases_only_unspent_reservation(monkeypatch, tmp_path):
    directory = Path(__file__).resolve().parents[2] / "agent"
    sys.path.insert(0, str(directory))
    import server

    requests = []
    context = {
        "action": {"kind": "qualify_batch", "payload": {}},
        "accounts": [{"text": "public fact " * 20000}],
    }

    def api(path, method="GET", body=None, lease=None, usage_token=None):
        if path.endswith("/context"):
            return context
        requests.append((path, body))
        return {}

    def unavailable(command, **options):
        assert max(map(len, command)) < 16000
        assert len(options["stdin"].read()) > 131072
        raise OSError("process creation failed before any model request")

    monkeypatch.setattr(server, "call", api)
    monkeypatch.setattr(server, "config", lambda: {"runtime": str(tmp_path)})
    monkeypatch.setattr(server.subprocess, "Popen", unavailable)
    server.execute(
        {
            "action": {"id": "startup-test", "kind": "qualify_batch"},
            "lease": "fake",
            "budget": {
                "run_id": "usage-test",
                "usage_token": "fake",
                "per_task_tokens": 200000,
                "max_output_tokens": 8000,
                "max_turns": 12,
                "max_seconds": 600,
            },
        }
    )
    assert requests[0][0] == "/usage/usage-test"
    assert requests[0][1]["complete_measurement"] is True and requests[0][1]["total_tokens"] == 0
    assert requests[1][0] == "/actions/startup-test/failure"
