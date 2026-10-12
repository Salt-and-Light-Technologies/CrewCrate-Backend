import uuid

from sqlalchemy import func, select
from test_api import OTHER, OWNER, USER, login
from test_api import client as client
from test_campaigns import config, ready_partner

from app.auth import current_actor
from app.database import get_session
from app.main import app
from app.models import AITestSession, CampaignEvent, Conversation, ConversationEvent


async def test_delete_related_records_retains_contacts_and_other_campaign(client):
    _, lead, url = await ready_partner(client)
    one = (await client.post(url + "/campaigns", json=config(lead))).json()
    two = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + one["id"]
    for campaign in (one, two):
        response = await client.post(url + "/campaigns/" + campaign["id"] + "/conversations",
                                     json={"lead_id": lead["id"], "note": "Manual tracking note"})
        assert response.status_code == 201
    async for session in app.dependency_overrides[get_session]():
        for campaign in (one, two):
            session.add(AITestSession(partner_id=uuid.UUID(one["partner_id"]), campaign_id=uuid.UUID(campaign["id"]),
                                      actor_id=USER, context={}, messages=[], decisions=[]))
        await session.commit()
    current = next(x for x in (await client.get(url + "/leads")).json() if x["id"] == lead["id"])
    reviewed = await client.post(url + "/leads/" + lead["id"] + "/contact-review", json={
        "expected_revision": current["revision"], "action": "opt_out", "evidence": "Customer requested no more texts",
    })
    assert reviewed.status_code == 200
    before = (await client.get(url + "/leads")).json()
    current_campaign = (await client.get(path)).json()
    assert (await client.delete(path, params={"expected_revision": current_campaign["revision"]})).status_code == 204
    assert (await client.get(path)).status_code == 404
    assert (await client.get(url + "/leads")).json() == before
    assert next(x for x in before if x["id"] == lead["id"])["opted_out"]
    assert (await client.get(url + "/campaigns/" + two["id"])).status_code == 200
    assert (await client.get(url + "/imports")).json()
    async for session in app.dependency_overrides[get_session]():
        for model in (CampaignEvent, Conversation, AITestSession):
            assert await session.scalar(select(func.count()).select_from(model).where(model.campaign_id == uuid.UUID(one["id"]))) == 0
            assert await session.scalar(select(func.count()).select_from(model).where(model.campaign_id == uuid.UUID(two["id"]))) == (2 if model is CampaignEvent else 1)
        assert await session.scalar(select(func.count()).select_from(ConversationEvent)) == 1
    history = (await client.get(url + "/history")).json()
    assert any(x["action"] == "campaign_deleted" and one["id"] in x["reason"] for x in history)


async def test_delete_auth_scope_stale_revision_and_owner_hold(client):
    _, lead, url = await ready_partner(client)
    campaign = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + campaign["id"]
    login(OTHER)
    assert (await client.delete(path, params={"expected_revision": campaign["revision"]})).status_code == 404
    app.dependency_overrides.pop(current_actor)
    assert (await client.delete(path, params={"expected_revision": campaign["revision"]})).status_code == 401
    login(USER)
    assert (await client.delete(path, params={"expected_revision": campaign["revision"] + 1})).status_code == 409
    login(OWNER)
    held = await client.post(path + "/control", json={"expected_revision": campaign["revision"], "action": "pause", "reason": "Administrator review hold"})
    assert held.status_code == 200 and held.json()["owner_hold"]
    login(USER)
    assert (await client.delete(path, params={"expected_revision": held.json()["revision"]})).status_code == 409
    assert (await client.get(path)).json()["owner_hold"]
