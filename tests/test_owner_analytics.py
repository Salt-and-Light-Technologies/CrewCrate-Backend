import uuid

from test_api import OTHER, OWNER, USER, create, login, upload
from test_api import client as client

from app.database import get_session
from app.main import app
from app.models import Conversation


async def test_only_business_can_create_campaigns_including_import(client):
    business = await create(client)
    path = f"/v1/partners/{business['id']}"
    assert (await client.post(path + "/campaigns", json={"name": "Owner attempt"})).status_code == 403
    assert (await client.post(path + "/campaign-import", data={
        "config": '{"name":"Owner import attempt"}', "expected_partner_revision": "0", "phone_column": "Phone",
    }, files={"file": ("contacts.csv", b"Phone\n13125550123", "text/csv")})).status_code == 403
    assert (await client.get(path + "/campaigns")).json() == []
    login(USER)
    assert (await client.post(path + "/campaigns", json={"name": "Business campaign"})).status_code == 201
    login(OTHER)
    assert (await client.post(path + "/campaigns", json={"name": "Foreign attempt"})).status_code == 404


async def test_owner_analytics_are_scoped_and_financial_values_unavailable(client):
    business = await create(client)
    other_business = await create(client, OTHER)
    login(USER)
    await upload(client, business["id"], 0, b"Phone,Name\n13125550101,One\n13125550102,Two\n")
    leads = (await client.get(f"/v1/partners/{business['id']}/leads")).json()
    campaign = (await client.post(f"/v1/partners/{business['id']}/campaigns", json={
        "name": "Business plan", "lead_ids": [leads[0]["id"]]})).json()
    login(OTHER)
    await upload(client, other_business["id"], 0, b"Phone,Name\n13125550201,One\n13125550202,Two\n13125550203,Three\n")
    other_leads = (await client.get(f"/v1/partners/{other_business['id']}/leads")).json()
    other_campaign = (await client.post(f"/v1/partners/{other_business['id']}/campaigns", json={
        "name": "Other plan", "lead_ids": [other_leads[0]["id"]]})).json()
    async for session in app.dependency_overrides[get_session]():
        session.add(Conversation(campaign_id=uuid.UUID(campaign["id"]), lead_id=uuid.UUID(leads[0]["id"]), status="closed"))
        session.add(Conversation(campaign_id=uuid.UUID(other_campaign["id"]), lead_id=uuid.UUID(other_leads[0]["id"]), status="appointment"))
        await session.commit()
    url = f"/v1/owner/partners/{business['id']}/analytics"
    assert (await client.get(url)).status_code == 403
    login(USER)
    assert (await client.get(url)).status_code == 403
    login(OWNER)
    result = await client.get(url)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["total_contacts"] == 2 and data["import_count"] == 1
    assert data["campaign_count"] == 1 and data["campaign_status_counts"] == {"draft": 1}
    assert data["manual_conversation_count"] == 1 and data["manual_status_counts"] == {"closed": 1}
    assert data["collected_revenue"] is None and data["direct_job_costs"] is None and data["commission_due"] is None
    assert data["sms_connected"] is False
    assert (await client.get(f"/v1/owner/partners/{uuid.uuid4()}/analytics")).status_code == 404
