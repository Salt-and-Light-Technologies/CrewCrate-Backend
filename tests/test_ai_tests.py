import json
import os
import uuid

os.environ.update(DATABASE_URL="sqlite+aiosqlite://", SUPABASE_URL="https://example.supabase.co", SUPABASE_PUBLISHABLE_KEY="test-key")

import httpx
import pytest
from fastapi import HTTPException
from test_api import OTHER, OWNER, USER, create, login, upload
from test_api import client as client

from app.ai_tests import AgentDecision, ResponsesAgent, get_agent
from app.auth import current_actor
from app.config import get_settings
from app.main import app


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.next = AgentDecision(reply="Tell me more.", decision="continue", reason="Needs information", handoff_summary="")

    async def generate(self, context, messages):
        self.calls.append((context, list(messages)))
        return self.next


@pytest.fixture
def agent():
    value = FakeAgent()
    app.dependency_overrides[get_agent] = lambda: value
    yield value
    app.dependency_overrides.pop(get_agent, None)


async def start(client, agent):
    partner = await create(client)
    login(USER)
    url = f"/v1/partners/{partner['id']}/ai-tests"
    response = await client.post(url, json={"config": {
        "name": "Roofing", "offer": "Free inspection", "ai_name": "Emma",
        "ai_brief": "Ask about roof leaks", "qualification": "Interested homeowner",
        "handoff_email": "sales@example.com",
    }})
    assert response.status_code == 201, response.text
    return partner, url, response.json()


async def send(client, url, test, text, request_id=None):
    return await client.post(f"{url}/{test['id']}/messages", json={
        "text": text, "expected_revision": test["revision"], "request_id": request_id or str(uuid.uuid4()),
    })


async def test_context_isolation_access_and_auth(client, agent):
    p, url, one = await start(client, agent)
    two = (await client.post(url, json={"config": {"name": "Other", "offer": "Another offer"}})).json()
    response = await send(client, url, one, "Is the inspection free?")
    assert response.status_code == 200
    one = response.json()
    assert one["simulated"] and not one["sending_enabled"] and one["source"] == "ai_test"
    context, messages = agent.calls[-1]
    assert context["business_name"] == p["name"]
    assert context["ai_name"] == "Emma" and context["offer"] == "Free inspection"
    assert context["ai_instructions"] == "Ask about roof leaks"
    assert context["qualification"] == "Interested homeowner"
    assert messages[-1]["text"] == "Is the inspection free?"
    assert len((await client.get(f"{url}/{two['id']}")).json()["messages"]) == 1
    login(OTHER)
    assert (await client.get(f"{url}/{one['id']}")).status_code == 404
    assert (await client.post(url, json={"config": {"name": "Unauthorized"}})).status_code == 404
    login(OWNER)
    assert (await client.get(f"{url}/{one['id']}")).status_code == 404
    app.dependency_overrides.pop(current_actor)
    assert (await client.get(f"{url}/{one['id']}")).status_code == 401


@pytest.mark.parametrize("text,status,decision", [
    ("STOP", "opted_out", "opt_out"), ("Please stop texting me!", "opted_out", "opt_out"),
    ("No thanks.", "ended", "end_outreach"), ("I'm not interested", "ended", "end_outreach"),
])
async def test_stop_and_no_live_mutation(client, agent, text, status, decision):
    p, url, test = await start(client, agent)
    partner_url = f"/v1/partners/{p['id']}"
    assert (await upload(client, p["id"], 0, b"Phone,Name\n12025550101,Fictional Lead\n")).status_code == 201
    before_leads = (await client.get(partner_url + "/leads")).json()
    before = (await client.get(partner_url)).json()
    stopped = (await send(client, url, test, text)).json()
    assert stopped["status"] == status and stopped["decisions"][-1]["decision"] == decision
    assert stopped["decisions"][-1]["simulated"] is True
    assert len(agent.calls) == 1
    assert (await send(client, url, stopped, "Actually tell me more")).status_code == 409
    assert (await client.get(partner_url)).json() == before
    assert (await client.get(partner_url + "/leads")).json() == before_leads
    assert not (await client.get("/v1/messaging/capabilities")).json()["outbound_enabled"]


async def test_handoff_idempotence_revisions(client, agent):
    _, url, test = await start(client, agent)
    agent.next = AgentDecision(reply="Ready for sales.", decision="handoff", reason="Requested sales",
                               handoff_summary="Lead asked for a call.", human_requested=True)
    request_id = str(uuid.uuid4())
    response = await send(client, url, test, "Have sales call me", request_id)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "handoff" and result["decisions"][-1]["handoff_summary"] == "Lead asked for a call."
    assert (await send(client, url, test, "Have sales call me", request_id)).json() == result
    assert len(agent.calls) == 2
    assert (await send(client, url, test, "Different text", request_id)).status_code == 409
    assert (await send(client, url, test, "Old revision")).status_code == 409


async def test_failure_rolls_back_turn(client, agent):
    _, url, test = await start(client, agent)
    async def unavailable(*args):
        raise HTTPException(502, "Provider unavailable")
    agent.generate = unavailable
    assert (await send(client, url, test, "What is the offer?")).status_code == 502
    assert (await client.get(f"{url}/{test['id']}")).json() == test


async def test_saved_campaign_source(client, agent):
    p = await create(client)
    login(USER)
    path = f"/v1/partners/{p['id']}"
    campaign = (await client.post(path + "/campaigns", json={"name": "Saved", "offer": "Saved offer"})).json()
    assert (await client.post(path + "/ai-tests", json={"campaign_id": campaign["id"]})).status_code == 201
    assert agent.calls[-1][0]["offer"] == "Saved offer"
    assert (await client.post(path + "/ai-tests", json={"campaign_id": str(uuid.uuid4())})).status_code == 404
    assert (await client.post(path + "/ai-tests", json={"campaign_id": campaign["id"], "config": {"name": "Override"}})).status_code == 422


async def test_responses_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    get_settings.cache_clear()
    bodies = []
    async_client = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert request.headers["Authorization"] == "Bearer fake-test-key"
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{
            "type": "output_text", "text": json.dumps(dict(reply="Hello", decision="continue", reason="Opening", handoff_summary="")),
        }]}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        result = await ResponsesAgent().generate({"offer": "Actual offer"}, [])
        assert result.decision == "continue"
        assert bodies[0]["store"] is False and "tools" not in bodies[0]
        assert bodies[0]["text"]["format"]["strict"] is True
        assert "Actual offer" in bodies[0]["instructions"]
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("status,payload", [
    (200, {"status": "incomplete", "output": []}),
    (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}),
    (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]}),
    (401, {"error": "secret-provider-details"}),
    (429, {}),
])
async def test_provider_errors_are_safe(monkeypatch, status, payload):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    get_settings.cache_clear()
    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)), **kwargs))
    try:
        with pytest.raises(HTTPException) as error:
            await ResponsesAgent().generate({}, [])
        assert error.value.status_code in (502, 503)
        assert "secret-provider-details" not in error.value.detail and "fake-test-key" not in error.value.detail
    finally:
        get_settings.cache_clear()


async def test_missing_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as error:
            await ResponsesAgent().generate({}, [])
        assert error.value.status_code == 503
    finally:
        get_settings.cache_clear()


async def test_business_context_and_session_limit(client, agent):
    from test_api import complete
    p = await create(client)
    path = f"/v1/partners/{p['id']}"
    assert (await client.put(path + "/onboarding", json={"expected_revision": 0, "onboarding": complete()})).status_code == 200
    login(USER)
    for _ in range(10):
        assert (await client.post(path + "/ai-tests", json={"config": {"name": "Test"}})).status_code == 201
    context = agent.calls[-1][0]
    assert context["business_name"] == "Any Industry"
    assert context["services"] == "Consultations" and context["service_area"] == "National"
    assert context["business_boundaries"] == "Escalate unknowns"
    assert (await client.post(path + "/ai-tests", json={"config": {"name": "Over limit"}})).status_code == 429
    assert len(agent.calls) == 10


async def test_turn_limit_and_failed_opening(client, agent):
    _, url, test = await start(client, agent)
    for _ in range(40):
        response = await send(client, url, test, "Tell me about the offer")
        assert response.status_code == 200
        test = response.json()
    assert (await send(client, url, test, "Another message")).status_code == 409
    agent.next = AgentDecision(reply="Stop", decision="opt_out", reason="Invalid opening", handoff_summary="")
    assert (await client.post(url, json={"config": {"name": "Bad opening"}})).status_code == 502


@pytest.mark.parametrize("first", [
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(dict(reply="Ready", decision="handoff", reason="Sales request", handoff_summary=""))}]}]},
])
async def test_provider_recovers_once(monkeypatch, first):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    get_settings.cache_clear()
    original_client = httpx.AsyncClient
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=first)
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{
            "type": "output_text", "text": json.dumps(dict(reply="I can help you learn more.", decision="continue", reason="Offer question", handoff_summary="")),
        }]}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        result = await ResponsesAgent().generate({"business_name": "MicLocker"}, [{"role": "user", "text": "Do you have other musicians?"}])
        assert result.decision == "continue"
        assert len(bodies) == 2
        assert [b["max_output_tokens"] for b in bodies] == [1600, 3200]
        assert bodies[0]["input"] == bodies[1]["input"]
        assert all(b["store"] is False and "tools" not in b for b in bodies)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("reason,expected_calls", [("max_output_tokens", 2), ("content_filter", 1)])
async def test_incomplete_retry_bounded_and_logs_private(monkeypatch, caplog, reason, expected_calls):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    get_settings.cache_clear()
    original_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "incomplete", "incomplete_details": {"reason": reason}, "output": []})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        with pytest.raises(HTTPException):
            await ResponsesAgent().generate({"business_name": "Private Business"}, [{"role": "user", "text": "Private lead message"}])
        assert len(calls) == expected_calls
        assert "Private Business" not in caplog.text
        assert "Private lead message" not in caplog.text
        assert "fake-test-key" not in caplog.text
        assert "category=" in caplog.text
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("signal,qualified,strong", [
    ("unknown", True, False), ("unmet", True, False),
    ("matched", True, True), ("matched", False, False),
])
async def test_persisted_additive_signal(client, agent, signal, qualified, strong):
    _, url, test = await start(client, agent)
    agent.next = AgentDecision(
        reply="Thanks for the details.", decision="continue", reason="Evaluated needs",
        handoff_summary="Lead wants roof help." if qualified else "",
        default_qualified=qualified, additional_signal=signal,
        signal_evidence="I am a homeowner", signal_reason="Lead confirms home ownership.",
    )
    response = await send(client, url, test, "I am a homeowner")
    assert response.status_code == 200
    saved = (await client.get(f"{url}/{test['id']}")).json()
    outcome = saved["decisions"][-1]
    assert saved["status"] == ("handoff" if qualified else "active")
    assert outcome["strong_response"] is strong
    assert outcome["signal_evidence"] == ("I am a homeowner" if signal == "matched" else "")
