import os
import uuid

os.environ.update(
    DATABASE_URL="sqlite+aiosqlite://",
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-key",
)
import httpx
import pytest
from app.auth import Actor, current_actor
from app.database import get_session
from app.imports import MAX_BYTES, inspect_csv
from app.main import app
from app.models import Base, Owner
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

OWNER, USER, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = database
    app.dependency_overrides[current_actor] = lambda: Actor(OWNER)
    async with sessions() as session:
        session.add(Owner(user_id=OWNER))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def create(client, user=USER):
    response = await client.post(
        "/v1/partners", json={"name": "Universal business", "partner_user_id": str(user)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(user):
    app.dependency_overrides[current_actor] = lambda: Actor(user)


def complete():
    return dict(
        contact_name="Administrator",
        email="person@example.com",
        business_name="Any Industry",
        industry="Manufacturing",
        services="Consultations",
        service_area="National",
        time_zone="America/Chicago",
        audience=["dormant"],
        goal="handoff",
        offer="Reconnect",
        qualification="Interested",
        sales_contact="Sales",
        handoff_email="sales@example.com",
        ai_boundaries="Escalate unknowns",
        lead_source="CRM",
        eligibility_notes="Documented evidence",
        reporting_system="Payments",
        commercial_terms="Proposed terms",
        fee_rate=10,
        accepts_visibility=True,
        confirms_accuracy=True,
    )


async def upload(
    client,
    pid,
    revision,
    content=b"Phone,Name\n+1 (555) 123-4567,First\n15551234567,Duplicate\nabc,Invalid\n",
):
    return await client.post(
        f"/v1/partners/{pid}/imports",
        data={"phone_column": "Phone", "name_column": "Name", "expected_revision": revision},
        files={"file": ("../../leads.csv", content, "text/csv")},
    )


async def test_partner_isolation_and_owner_privileges(client):
    a = await create(client)
    b = await create(client, OTHER)
    login(USER)
    response = await client.get("/v1/partners")
    assert [p["id"] for p in response.json()] == [a["id"]]
    for suffix in ("", "/history", "/imports", "/leads"):
        assert (await client.get("/v1/partners/" + b["id"] + suffix)).status_code == 404
    assert (await client.get("/v1/owner/overview")).status_code == 403
    assert (
        await client.post("/v1/partners", json={"name": "Fake", "partner_user_id": str(USER)})
    ).status_code == 403
    assert (
        await client.post(
            f"/v1/partners/{a['id']}/decisions", json={"expected_revision": 0, "action": "approve"}
        )
    ).status_code == 403
    assert (
        await client.put(
            f"/v1/partners/{a['id']}/readiness",
            json={"expected_revision": 0, "reason": "fake review", "readiness": {}},
        )
    ).status_code == 403
    assert (
        await client.put(
            f"/v1/partners/{b['id']}/onboarding", json={"expected_revision": 0, "onboarding": complete()}
        )
    ).status_code == 404
    assert (await upload(client, b["id"], 0)).status_code == 404


async def test_full_workflow_and_revision_conflicts(client):
    p = await create(client)
    pid = p["id"]
    url = "/v1/partners/" + pid
    login(USER)
    invalid = await client.post(url + "/submit", json={"expected_revision": 0})
    assert invalid.status_code == 422
    saved = await client.put(url + "/onboarding", json={"expected_revision": 0, "onboarding": complete()})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    stale = await client.put(url + "/onboarding", json={"expected_revision": 0, "onboarding": complete()})
    assert stale.status_code == 409
    imported = await upload(client, pid, 1)
    assert imported.status_code == 201, imported.text
    assert {k: imported.json()[k] for k in ("imported", "duplicates", "invalid", "total_rows")} == dict(
        imported=1, duplicates=1, invalid=1, total_rows=3
    )
    assert imported.json()["file_name"] == "leads.csv"
    response = await client.post(url + "/submit", json={"expected_revision": 2})
    assert response.status_code == 200 and response.json()["status"] == "submitted"
    assert (
        await client.put(url + "/onboarding", json={"expected_revision": 3, "onboarding": complete()})
    ).status_code == 409
    login(OWNER)
    assert (
        await client.post(url + "/decisions", json={"expected_revision": 3, "action": "approve"})
    ).status_code == 422
    ready = await client.put(
        url + "/readiness",
        json={
            "expected_revision": 3,
            "reason": "Evidence reviewed",
            "readiness": dict(
                eligibility_reviewed=True,
                messaging_ready=True,
                reporting_ready=True,
                agreement_finalized=True,
            ),
        },
    )
    assert ready.status_code == 200
    approved = await client.post(url + "/decisions", json={"expected_revision": 4, "action": "approve"})
    assert approved.status_code == 200 and approved.json()["status"] == "pilot_approved"
    assert (
        await client.post(
            url + "/decisions", json={"expected_revision": 4, "action": "pause", "reason": "Review records"}
        )
    ).status_code == 409
    paused = await client.post(
        url + "/decisions", json={"expected_revision": 5, "action": "pause", "reason": "Review records"}
    )
    assert paused.json()["status"] == "paused"
    resumed = await client.post(url + "/decisions", json={"expected_revision": 6, "action": "resume"})
    assert resumed.json()["status"] == "pilot_approved" and resumed.json()["paused_from"] is None
    history = (await client.get(url + "/history")).json()
    assert [e["revision"] for e in history] == list(range(7, 0, -1))
    assert history[0]["actor_id"] == str(OWNER)
    assert history[-1]["actor_id"] == str(USER)
    overview = (await client.get("/v1/owner/overview")).json()
    assert overview["partner_status_counts"]["pilot_approved"] == 1
    assert overview["lead_status_counts"]["unreviewed"] == 1


async def test_change_requests_and_readiness_reset(client):
    p = await create(client)
    url = "/v1/partners/" + p["id"]
    await client.put(url + "/onboarding", json={"expected_revision": 0, "onboarding": complete()})
    await client.post(url + "/submit", json={"expected_revision": 1})
    empty = await client.post(
        url + "/decisions", json={"expected_revision": 2, "action": "request_changes", "reason": "     "}
    )
    assert empty.status_code == 422
    ready = await client.put(
        url + "/readiness",
        json={
            "expected_revision": 2,
            "reason": "Reviewed evidence",
            "readiness": {"eligibility_reviewed": True},
        },
    )
    assert ready.status_code == 200
    changed = await client.post(
        url + "/decisions",
        json={"expected_revision": 3, "action": "request_changes", "reason": "Correct the qualification"},
    )
    assert changed.json()["status"] == "changes_requested"
    login(USER)
    saved = await client.put(url + "/onboarding", json={"expected_revision": 4, "onboarding": complete()})
    assert not any(saved.json()["readiness"].values())
    assert (await client.post(url + "/submit", json={"expected_revision": 5})).json()["status"] == "submitted"


async def test_import_dedup_and_lead_scope(client):
    p = await create(client)
    other = await create(client, OTHER)
    first = await upload(client, p["id"], 0)
    assert first.status_code == 201
    second = await upload(client, p["id"], 1)
    assert second.json()["imported"] == 0 and second.json()["duplicates"] == 2
    # Same contact in a separate partner is allowed.
    assert (await upload(client, other["id"], 0)).json()["imported"] == 1
    login(USER)
    lead = (await client.get(f"/v1/partners/{p['id']}/leads")).json()[0]
    assert (
        await client.patch(
            f"/v1/partners/{p['id']}/leads/{lead['id']}", json={"expected_revision": 0, "status": "eligible"}
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/v1/partners/{p['id']}/leads/{lead['id']}", json={"expected_revision": 0, "status": "excluded"}
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/v1/partners/{other['id']}/leads/{lead['id']}",
            json={"expected_revision": 1, "status": "excluded"},
        )
    ).status_code == 404
    assert (await client.get(f"/v1/partners/{p['id']}/leads?limit=101")).status_code == 422


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"Phone,Phone\n1234567,1234567",
        b'Phone\n"unclosed',
        b"Phone,Name\n1234567",
        b"\xff",
        b"Phone\n" + b"1" * (MAX_BYTES + 1),
    ],
)
def test_malformed_csv(raw):
    with pytest.raises(HTTPException):
        inspect_csv(raw, "Phone", None, None)


def test_csv_quoted_fields_and_invalid_phones():
    rows, total, invalid = inspect_csv(
        b'Phone,Name\n1234567,"Name, with comma"\n12345678abc,Bad\n', "Phone", "Name", None
    )
    assert total == 2 and invalid == 1 and rows[0]["name"] == "Name, with comma"


async def test_auth_headers_and_remote_verification(monkeypatch):
    with pytest.raises(HTTPException) as error:
        await current_actor(None)
    assert error.value.status_code == 401

    # Supabase verifies the token; no unverified JWT claims establish owner access.
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers):
            assert url.endswith("/auth/v1/user")
            assert headers["Authorization"] == "Bearer token"
            return httpx.Response(200, json={"id": str(USER)})

    monkeypatch.setattr("app.auth.httpx.AsyncClient", lambda **kwargs: Client())
    assert (await current_actor("Bearer token")).user_id == USER


async def test_body_limit_and_no_live_auth_override(client):
    response = await client.post("/v1/partners", content=b"x" * (6 * 1024 * 1024 + 1))
    assert response.status_code == 413
    app.dependency_overrides.pop(current_actor)
    assert (await client.get("/v1/partners")).status_code == 401


async def test_supabase_invalid_token_and_outage(monkeypatch):
    status = 401

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers):
            return httpx.Response(status)

    monkeypatch.setattr("app.auth.httpx.AsyncClient", lambda **kwargs: Client())
    with pytest.raises(HTTPException) as error:
        await current_actor("Bearer invalid")
    assert error.value.status_code == 401
    status = 500
    with pytest.raises(HTTPException) as error:
        await current_actor("Bearer invalid")
    assert error.value.status_code == 503


async def test_preview_is_read_only_and_reports_existing_duplicates(client):
    p = await create(client)
    url = "/v1/partners/" + p["id"]
    data = {"phone_column": "Phone", "name_column": "Name"}
    csv = b"Phone,Name\n1234567,First\n1234567,Duplicate\nabc,Invalid\n"
    response = await client.post(
        url + "/imports/preview", data=data, files={"file": ("leads.csv", csv, "text/csv")}
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["partner_revision"] == 0
    assert (preview["imported"], preview["duplicates"], preview["invalid"]) == (1, 1, 1)
    assert [(x["row_number"], x["kind"]) for x in preview["issues"]] == [(2, "duplicate"), (3, "invalid")]
    assert (await client.get(url + "/lead-workspace")).json()["total_leads"] == 0
    assert (await client.get(url + "/imports")).json() == []
    assert (await client.get(url + "/lead-activity")).json() == []
    assert (await upload(client, p["id"], 0, csv)).status_code == 201
    again = await client.post(
        url + "/imports/preview", data=data, files={"file": ("leads.csv", csv, "text/csv")}
    )
    assert again.json()["partner_revision"] == 1 and again.json()["duplicates"] == 2
    # A preview revision is checked again at commit time.
    assert (await upload(client, p["id"], 0, csv)).status_code == 409
    summary = (await client.get(url + "/lead-workspace")).json()
    assert summary["lead_counts"] == {"unreviewed": 1} and summary["import_count"] == 1
    activity = (await client.get(url + "/lead-activity")).json()
    assert len(activity) == 1 and activity[0]["action"] == "leads_imported"


async def test_lead_filters_literal_search_and_owner_summaries(client):
    a = await create(client)
    b = await create(client, OTHER)
    assert (
        await upload(client, a["id"], 0, b"Phone,Name\n1234567,Alpha 100%\n1234568,Beta\n")
    ).status_code == 201
    assert (await upload(client, b["id"], 0, b"Phone,Name\n1234569,Alpha Secret\n")).status_code == 201
    summaries = (await client.get("/v1/owner/lead-workspaces")).json()
    assert {x["partner_id"]: x["total_leads"] for x in summaries} == {a["id"]: 2, b["id"]: 1}
    login(USER)
    url = "/v1/partners/" + a["id"]
    assert len((await client.get(url + "/leads?search=%25")).json()) == 1
    assert (await client.get(url + "/leads?search=secret")).json() == []
    first = (await client.get(url + "/leads?search=alpha")).json()[0]
    assert (
        await client.patch(url + "/leads/" + first["id"], json={"expected_revision": 0, "status": "excluded"})
    ).status_code == 200
    assert len((await client.get(url + "/leads?status=excluded")).json()) == 1
    assert len((await client.get(url + "/leads?status=unreviewed")).json()) == 1
    assert (await client.get("/v1/owner/lead-workspaces")).status_code == 403
    for suffix in ("/lead-workspace", "/lead-activity"):
        assert (await client.get("/v1/partners/" + b["id"] + suffix)).status_code == 404
    assert (
        await client.post(
            "/v1/partners/" + b["id"] + "/imports/preview",
            data={"phone_column": "Phone"},
            files={"file": ("a.csv", b"Phone\n1234567", "text/csv")},
        )
    ).status_code == 404


@pytest.mark.parametrize(
    "headers,mapping",
    [
        (" Phone ,phone", {"phone_column": "Phone"}),
        ("Phone,Name", {"phone_column": "Phone", "name_column": "Phone"}),
    ],
)
async def test_preview_rejects_ambiguous_mapping(client, headers, mapping):
    p = await create(client)
    response = await client.post(
        "/v1/partners/" + p["id"] + "/imports/preview",
        data=mapping,
        files={"file": ("a.csv", (headers + "\n1234567,Alpha").encode(), "text/csv")},
    )
    assert response.status_code == 422


async def test_additional_uploads_after_onboarding_and_list_filter(client):
    p = await create(client)
    pid = p["id"]
    url = "/v1/partners/" + pid
    login(USER)
    assert (await client.put(url + "/onboarding", json={"expected_revision": 0, "onboarding": complete()})).status_code == 200
    assert (await client.post(url + "/submit", json={"expected_revision": 1})).status_code == 200
    first = await upload(client, pid, 2, b"Phone,Name\n+12025550101,First\n")
    assert first.status_code == 201, first.text
    second = await upload(client, pid, 3, b"Phone,Name\n+12025550102,Second\n")
    assert second.status_code == 201, second.text
    batches = (await client.get(url + "/imports")).json()
    assert len(batches) == 2
    selected = await client.get(url + "/leads", params={"import_id": second.json()["id"]})
    assert selected.status_code == 200
    assert len(selected.json()) == 1 and selected.json()[0]["name"] == "Second"
    assert (await client.get(url)).json()["status"] == "submitted"
    other = await client.get(url + "/leads", params={"import_id": str(uuid.uuid4())})
    assert other.json() == []
    login(OTHER)
    assert (await client.get(url + "/leads", params={"import_id": second.json()["id"]})).status_code == 404
