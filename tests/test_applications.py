from test_api import OTHER, OWNER, USER, client, login  # noqa: F401

APPLICATION = {
    "business_name": "Bob's Roofing", "contact_name": "Bob",
    "email": "bob@example.com", "phone": "+13125550123", "referral": "Joe's Flooring",
}


async def test_pending_application_is_private_and_grants_no_access(client):  # noqa: F811
    login(USER)
    assert (await client.get("/v1/me/application")).json() is None
    response = await client.post("/v1/me/application", json=APPLICATION)
    assert response.status_code == 200, response.text
    application = response.json()
    assert application["status"] == "pending"
    assert application["partner_id"] is None
    assert (await client.get("/v1/partners")).json() == []
    assert (await client.post("/v1/me/business", json={"business_name": "Bypass"})).status_code == 403
    assert (await client.get("/v1/owner/applications")).status_code == 403
    assert (await client.post("/v1/me/application", json=APPLICATION)).json()["id"] == application["id"]
    login(OTHER)
    assert (await client.get("/v1/me/application")).json() is None
    assert (await client.post(f"/v1/owner/applications/{application['id']}/decision", json={
        "action": "approve", "expected_revision": 0})).status_code == 403
    login(OWNER)
    assert [item["id"] for item in (await client.get("/v1/owner/applications")).json()] == [application["id"]]


async def test_approval_creates_one_isolated_workspace_and_checks_revision(client):  # noqa: F811
    login(USER)
    application = (await client.post("/v1/me/application", json=APPLICATION)).json()
    login(OWNER)
    url = f"/v1/owner/applications/{application['id']}/decision"
    assert (await client.post(url, json={"action": "approve", "expected_revision": 1})).status_code == 409
    response = await client.post(url, json={"action": "approve", "expected_revision": 0})
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved["status"] == "approved" and approved["partner_id"]
    assert (await client.post(url, json={"action": "approve", "expected_revision": 0})).status_code == 409
    assert (await client.get("/v1/owner/applications")).json() == []
    login(USER)
    businesses = (await client.get("/v1/partners")).json()
    assert len(businesses) == 1
    assert businesses[0]["id"] == approved["partner_id"]
    assert businesses[0]["status"] == "draft"
    assert businesses[0]["readiness"]["messaging_ready"] is False
    login(OTHER)
    assert (await client.get(f"/v1/partners/{approved['partner_id']}")).status_code == 404


async def test_denial_requires_reason_and_never_creates_membership(client):  # noqa: F811
    login(USER)
    application = (await client.post("/v1/me/application", json=APPLICATION)).json()
    login(OWNER)
    url = f"/v1/owner/applications/{application['id']}/decision"
    assert (await client.post(url, json={"action": "deny", "expected_revision": 0})).status_code == 422
    result = await client.post(url, json={"action": "deny", "expected_revision": 0, "note": "Please contact us"})
    assert result.status_code == 200
    login(USER)
    assert (await client.get("/v1/me/application")).json()["review_note"] == "Please contact us"
    assert (await client.get("/v1/partners")).json() == []


async def test_application_rejects_identity_injection_and_invalid_contact(client):  # noqa: F811
    login(USER)
    assert (await client.post("/v1/me/application", json={**APPLICATION, "user_id": str(OTHER)})).status_code == 422
    assert (await client.post("/v1/me/application", json={**APPLICATION, "email": "invalid"})).status_code == 422
    assert (await client.post("/v1/me/application", json={**APPLICATION, "business_name": " "})).status_code == 422
    assert (await client.post("/v1/me/application", json={**APPLICATION, "phone": "invalid-phone"})).status_code == 422


async def test_legacy_membership_cannot_bypass_approval_and_review_reuses_workspace(client):  # noqa: F811
    import uuid

    from test_api import create

    from app.database import get_session
    from app.main import app
    from app.models import Partner

    business = await create(client)
    business_id = business["id"]
    # Simulate a membership created by the earlier self-service flow with no owner approval.
    async for session in app.dependency_overrides[get_session]():
        row = await session.get(Partner, uuid.UUID(business_id))
        row.access_approved = False
        row.onboarding = {"services": "Existing services must survive approval"}
        await session.commit()
    login(USER)
    assert (await client.get("/v1/partners")).json() == []
    assert (await client.get(f"/v1/partners/{business_id}")).status_code == 403
    assert (await client.get(f"/v1/partners/{business_id}/campaigns")).status_code == 403
    assert (await client.get(f"/v1/partners/{business_id}/leads")).status_code == 403
    assert (await client.post("/v1/me/business", json={"business_name": "Bypass"})).status_code == 403
    submitted = await client.post("/v1/me/application", json=APPLICATION)
    assert submitted.status_code == 200, submitted.text
    application = submitted.json()
    assert application["partner_id"] == business_id and application["status"] == "pending"
    login(OWNER)
    assert (await client.get(f"/v1/partners/{business_id}")).status_code == 200
    approved = await client.post(f"/v1/owner/applications/{application['id']}/decision", json={
        "expected_revision": 0, "action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["partner_id"] == business_id
    login(USER)
    businesses = (await client.get("/v1/partners")).json()
    assert len(businesses) == 1 and businesses[0]["access_approved"] is True
    assert businesses[0]["onboarding"]["services"] == "Existing services must survive approval"
    assert (await client.get(f"/v1/partners/{business_id}/campaigns")).status_code == 200
