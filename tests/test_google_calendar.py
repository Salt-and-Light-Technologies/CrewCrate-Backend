import json
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import SecretStr
from test_api import OTHER, OWNER, USER, create, login
from test_api import client as api_client

from app.auth import Actor, current_actor
from app.config import get_settings
from app.database import get_session
from app.google_calendar import SCOPES, GoogleProvider, now, provider
from app.main import app
from app.models import CalendarConnection, CalendarOAuthState, Member, Partner

client = api_client
WORKER = uuid.uuid4()
EVENT = {"id": "event1", "etag": '"version1"', "summary": "Customer visit", "description": "Bring supplies",
    "location": "Office", "start": {"dateTime": "2026-10-12T09:00:00-05:00"},
    "end": {"dateTime": "2026-10-12T10:00:00-05:00"}}


class FakeGoogle:
    def __init__(self):
        self.calls = []
        self.scope = " ".join(SCOPES)
        self.fail = False
        self.etag = EVENT["etag"]
        self.pages = 0
        self.role = "writer"
        self.token_calls = []

    async def token(self, data):
        self.token_calls.append(data)
        if self.fail:
            raise HTTPException(409, "Google access expired or was revoked")
        return {"access_token": "private-access", "refresh_token": "private-refresh",
                "expires_in": 3600, "scope": self.scope}

    async def request(self, method, path, token, *, params=None, body=None, etag=None):
        self.calls.append((method, path, params, body, etag))
        if self.fail:
            raise HTTPException(502, "Google is unavailable")
        if path == "users/me/calendarList":
            return {"items": [{"id": "business@example.com", "summary": "Team schedule", "timeZone": "America/Chicago", "accessRole": self.role},
                {"id": "private", "summary": "Personal read-only", "accessRole": "reader"}]}
        if path.startswith("users/me/calendarList/"):
            return {"id": "business@example.com", "summary": "Team schedule", "timeZone": "America/Chicago", "accessRole": self.role}
        if method == "GET" and path.endswith("/events"):
            self.pages += 1
            return {"items": [EVENT], **({"nextPageToken": "next"} if self.pages < 2 else {})}
        if method == "GET":
            return {**EVENT, "etag": self.etag}
        if method == "DELETE":
            return {}
        return {**EVENT, **(body or {}), "etag": '"version2"'}


@pytest.fixture
async def google(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_calendar_client_id", "test-client")
    monkeypatch.setattr(settings, "google_calendar_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "google_calendar_redirect_uri", "https://crewcrate-api.onrender.com/v1/integrations/google-calendar/callback")
    monkeypatch.setattr(settings, "google_calendar_token_key", SecretStr(Fernet.generate_key().decode()))
    fake = FakeGoogle()
    app.dependency_overrides[provider] = lambda: fake
    yield fake


async def db(action):
    async for session in app.dependency_overrides[get_session]():
        return await action(session)


async def start(client, pid):
    login(USER)
    response = await client.post(f"/v1/partners/{pid}/google-calendar/connect")
    assert response.status_code == 200, response.text
    query = parse_qs(urlparse(response.json()["authorization_url"]).query)
    assert set(query["scope"][0].split()) == SCOPES
    assert query["access_type"] == ["offline"] and query["code_challenge_method"] == ["S256"]
    return query["state"][0]


async def linked(client, google):
    login(OWNER)
    pid = (await create(client))["id"]
    state = await start(client, pid)
    response = await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})
    assert response.status_code == 200, response.text
    result = await client.put(f"/v1/partners/{pid}/google-calendar/selection", json={"calendar_id": "business@example.com", "expected_revision": 1})
    assert result.status_code == 200, result.text
    return pid, result.json()["revision"]


async def worker(pid, active=True):
    async def seed(session):
        session.add(Member(partner_id=uuid.UUID(pid), user_id=WORKER, role="worker", active=active))
        await session.commit()
    await db(seed)
    app.dependency_overrides[current_actor] = lambda: Actor(WORKER)


def body(revision, **changes):
    return {"title": "New appointment", "start": EVENT["start"], "end": EVENT["end"],
        "calendar_revision": revision, "request_id": str(uuid.uuid4()), **changes}


async def test_oauth_encryption_single_use_selection_and_no_token_disclosure(client, google):
    pid, rev = await linked(client, google)
    assert rev == 2
    state = await start(client, pid)
    async def check(session):
        row = await session.get(CalendarOAuthState, __import__('hashlib').sha256(state.encode()).hexdigest())
        assert row.verifier != google.token_calls[0]["code_verifier"]
        stored = await session.get(CalendarConnection, uuid.UUID(pid))
        assert "private" not in stored.access_token and "private" not in stored.refresh_token
    await db(check)
    response = await client.get(f"/v1/partners/{pid}/google-calendar")
    assert "private-access" not in response.text and "refresh_token" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})).status_code == 200
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})).status_code == 400
    assert (await client.get(f"/v1/partners/{pid}/google-calendar")).json()["calendar_id"] is None
    choices = (await client.get(f"/v1/partners/{pid}/google-calendar/calendars")).json()
    assert [x["id"] for x in choices] == ["business@example.com"]


async def test_worker_reads_and_writes_only_selected_business_calendar(client, google):
    pid, rev = await linked(client, google)
    await worker(pid)
    path = f"/v1/partners/{pid}/google-calendar"
    for method, suffix, args in [("post", "/connect", {}), ("get", "/calendars", {}),
        ("put", "/selection", {"json": {"calendar_id": "private", "expected_revision": rev}}),
        ("delete", "", {"params": {"expected_revision": rev}})]:
        assert (await getattr(client, method)(path + suffix, **args)).status_code == 403
    events = await client.get(path + "/events", params={"start": "2026-10-12T00:00:00Z", "end": "2026-10-19T00:00:00Z"})
    assert events.status_code == 200 and len(events.json()["events"]) == 2
    saved = await client.post(path + "/events", json=body(rev))
    assert saved.status_code == 201, saved.text
    created_call = google.calls[-1]
    assert created_call[1] == "calendars/business%40example.com/events"
    assert len(created_call[3]["id"]) == 64
    updated = await client.patch(path + "/events/event1", json=body(rev, etag=EVENT["etag"]))
    assert updated.status_code == 200 and google.calls[-1][-1] == EVENT["etag"]
    assert google.calls[-1][2] == {"sendUpdates": "all"}
    deleted = await client.delete(path + "/events/event1", params={"etag": EVENT["etag"], "calendar_revision": rev})
    assert deleted.status_code == 200
    login(OTHER)
    for method, suffix, args in [("get", "", {}), ("get", "/calendars", {}), ("post", "/connect", {}),
        ("get", "/events", {"params": {"start": "2026-10-12T00:00:00Z", "end": "2026-10-19T00:00:00Z"}}),
        ("post", "/events", {"json": body(rev)}), ("patch", "/events/event1", {"json": body(rev, etag=EVENT["etag"])}),
        ("delete", "/events/event1", {"params": {"etag": EVENT["etag"], "calendar_revision": rev}})]:
        assert (await getattr(client, method)(path + suffix, **args)).status_code == 404
    login(OWNER)
    assert (await client.post(path + "/connect")).status_code == 404
    assert (await client.get(path)).status_code == 404


async def test_inactive_workers_and_pending_business_denied(client, google):
    pid, rev = await linked(client, google)
    await worker(pid, active=False)
    assert (await client.get(f"/v1/partners/{pid}/google-calendar")).status_code == 404
    assert (await client.post(f"/v1/partners/{pid}/google-calendar/events", json=body(rev))).status_code == 404
    async def pending(session):
        row = await session.get(Partner, uuid.UUID(pid))
        row.access_approved = False
        await session.commit()
    await db(pending)
    login(USER)
    assert (await client.post(f"/v1/partners/{pid}/google-calendar/connect")).status_code == 403


async def test_expired_cancelled_superseded_and_revoked_admin_oauth(client, google):
    pid = (await create(client))["id"]
    expired = await start(client, pid)
    async def expire(session):
        row = await session.get(CalendarOAuthState, __import__('hashlib').sha256(expired.encode()).hexdigest())
        row.expires_at = now() - timedelta(seconds=1)
        await session.commit()
    await db(expire)
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": expired, "code": "code"})).status_code == 400
    old = await start(client, pid)
    state = await start(client, pid)
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": old, "code": "code"})).status_code == 400
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "error": "access_denied"})).status_code == 400
    assert not google.token_calls
    state = await start(client, pid)
    async def deactivate(session):
        member = await session.get(Member, (uuid.UUID(pid), USER))
        member.active = False
        await session.commit()
    await db(deactivate)
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})).status_code == 403
    assert not google.token_calls


async def test_partial_scopes_fail_without_replacing_connection(client, google):
    pid, _ = await linked(client, google)
    state = await start(client, pid)
    google.scope = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})).status_code == 400
    assert (await client.get(f"/v1/partners/{pid}/google-calendar")).json()["calendar_name"] == "Team schedule"
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})).status_code == 400


async def test_stale_event_selection_dates_and_disconnect(client, google):
    pid, rev = await linked(client, google)
    path = f"/v1/partners/{pid}/google-calendar"
    assert (await client.post(path + "/events", json=body(rev-1))).status_code == 409
    google.etag = '"new-version"'
    assert (await client.patch(path + "/events/event1", json=body(rev, etag=EVENT["etag"]))).status_code == 409
    assert (await client.delete(path + "/events/event1", params={"etag": EVENT["etag"], "calendar_revision": rev})).status_code == 409
    assert (await client.patch(path + "/events/event1", json=body(rev))).status_code == 422
    for start, end in [("2026-10-12", "2026-12-12"), ("2026-10-12T00:00:00", "2026-10-19T00:00:00"),
                       ("2026-10-19T00:00:00Z", "2026-10-12T00:00:00Z")]:
        assert (await client.get(path + "/events", params={"start": start, "end": end})).status_code == 422
    assert (await client.post(path + "/events", json=body(rev, end=EVENT["start"]))).status_code == 422
    assert (await client.post(path + "/events", json=body(rev, start={"date": "2026-10-12"}, end={"date": "2026-10-13"}))).status_code == 201
    google.role = "reader"
    assert (await client.put(path + "/selection", json={"calendar_id": "private", "expected_revision": rev})).status_code == 422
    state = await start(client, pid)
    assert (await client.delete(path, params={"expected_revision": rev})).status_code == 200
    status = (await client.get(path)).json()
    assert not status["connected"] and status["revision"] > rev
    assert (await client.get("/v1/integrations/google-calendar/callback", params={"state": state, "code": "code"})).status_code == 400
    assert (await client.post(path + "/events", json=body(rev))).status_code == 409


async def test_refresh_failure_and_unconfigured_integration(client, google, monkeypatch):
    pid, rev = await linked(client, google)
    async def expire(session):
        row = await session.get(CalendarConnection, uuid.UUID(pid))
        row.access_expires_at = now() - timedelta(seconds=1)
        await session.commit()
    await db(expire)
    assert (await client.post(f"/v1/partners/{pid}/google-calendar/events", json=body(rev))).status_code == 201
    assert google.token_calls[-1]["grant_type"] == "refresh_token"
    await db(expire)
    google.fail = True
    assert (await client.post(f"/v1/partners/{pid}/google-calendar/events", json=body(rev))).status_code == 409
    monkeypatch.setattr(get_settings(), "google_calendar_client_id", "")
    assert (await client.post(f"/v1/partners/{pid}/google-calendar/connect")).status_code == 503


async def test_provider_http_maps_errors_and_sends_etag(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []
    async def respond(request):
        seen.append(request)
        if request.method == "PATCH":
            return httpx.Response(412)
        if request.url.path.endswith("/token"):
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "secret"})
        return httpx.Response(200, json=EVENT)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    provider = GoogleProvider()
    with pytest.raises(HTTPException) as conflict:
        await provider.request("PATCH", "calendars/team/events/event1", "server-token", body={"summary": "Changed"}, etag='"v1"')
    assert conflict.value.status_code == 409
    assert seen[-1].headers["If-Match"] == '"v1"' and json.loads(seen[-1].content) == {"summary": "Changed"}
    with pytest.raises(HTTPException) as revoked:
        await provider.token({"refresh_token": "secret"})
    assert revoked.value.status_code == 409 and "secret" not in revoked.value.detail
