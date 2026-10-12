import base64
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_api import OTHER, OWNER, USER, create, login
from test_api import client as api_client

from app.auth import Actor, current_actor
from app.database import get_session
from app.main import app
from app.models import (
    Campaign,
    Conversation,
    ConversationEvent,
    Lead,
    LeadImport,
    WorkerDevice,
    WorkerInvitation,
    WorkerNotification,
    WorkerPushJob,
)
from app.worker_push import dispatch_batch, provider_token
from app.workers import aware, notify_interested, now

client = api_client

WORKER, SECOND = uuid.uuid4(), uuid.uuid4()


def worker_login(user=WORKER, email="worker@example.com", verified=True):
    app.dependency_overrides[current_actor] = lambda: Actor(user, email, verified)


async def issue(client, partner, email="worker@example.com"):
    login(USER)
    response = await client.post(f"/v1/partners/{partner}/worker-invitations", json={"email": email})
    assert response.status_code == 201, response.text
    return response.json()


async def join(client, token, user=WORKER, email="worker@example.com", verified=True):
    worker_login(user, email, verified)
    return await client.post("/v1/me/worker-invitations/accept", json={"token": token, "display_name": "Alex Worker"})


async def database(action):
    async for session in app.dependency_overrides[get_session]():
        return await action(session)


async def seed_conversation(partner):
    async def seed(session):
        batch = LeadImport(partner_id=partner, actor_id=USER, file_name="test.csv", total_rows=1,
            imported=1, duplicates=0, invalid=0)
        session.add(batch)
        await session.flush()
        lead = Lead(partner_id=partner, import_id=batch.id, name="Pat", phone="15551234567", email="pat@example.com")
        campaign = Campaign(partner_id=partner, config={"name": "Test"})
        session.add_all([lead, campaign])
        await session.flush()
        c = Conversation(campaign_id=campaign.id, lead_id=lead.id, status="new")
        session.add(c)
        await session.flush()
        session.add(ConversationEvent(conversation_id=c.id, actor_id=USER, status="new", note="Manual record", revision=0))
        await session.commit()
        return c.id, campaign.id
    return await database(seed)


async def test_verified_recipient_single_use_and_worker_permissions(client):
    partner = (await create(client))["id"]
    invitation = await issue(client, partner)
    token = invitation["token"]
    assert (await join(client, token, verified=False)).status_code == 403
    assert (await join(client, token, email="other@example.com")).status_code == 403
    assert (await join(client, token)).status_code == 200
    assert (await join(client, token)).status_code == 404
    businesses = (await client.get("/v1/partners")).json()
    assert businesses[0]["member_role"] == "worker"
    assert businesses[0]["onboarding"]["email"] == ""
    for method, path, body in [
        ("get", "team", None), ("get", "campaigns", None), ("get", "leads", None),
        ("post", "worker-invitations", {"email": "x@example.com"}),
        ("put", "business", {"expected_revision": 0, "business_name": "Bypass"}),
    ]:
        kwargs = {"json": body} if body is not None else {}
        response = await getattr(client, method)(f"/v1/partners/{partner}/{path}", **kwargs)
        assert response.status_code == 403, response.text
    assert (await client.get(f"/v1/partners/{partner}/incoming-leads")).status_code == 200
    async def check_hash(session):
        row = await session.get(WorkerInvitation, uuid.UUID(invitation["id"]))
        assert row.token_hash != token and len(row.token_hash) == 64
        assert row.consumed
    await database(check_hash)


async def test_expiry_rotation_revocation_and_team_scope(client):
    partner = (await create(client))["id"]
    first = await issue(client, partner)
    second = await issue(client, partner)
    assert (await join(client, first["token"])).status_code == 404
    async def expire(session):
        row = await session.get(WorkerInvitation, uuid.UUID(second["id"]))
        row.expires_at = now() - timedelta(seconds=1)
        await session.commit()
    await database(expire)
    assert (await join(client, second["token"])).status_code == 404
    third = await issue(client, partner)
    login(OTHER)
    assert (await client.get(f"/v1/partners/{partner}/team")).status_code == 404
    login(USER)
    assert (await client.delete(f"/v1/partners/{partner}/worker-invitations/{third['id']}")).status_code == 200
    assert (await join(client, third["token"])).status_code == 404
    login(OWNER)
    assert (await client.post(f"/v1/partners/{partner}/worker-invitations", json={"email": "x@example.com"})).status_code == 403


async def test_inbox_business_isolation_followup_and_removal(client):
    partner = uuid.UUID((await create(client))["id"])
    invitation = await issue(client, partner)
    assert (await join(client, invitation["token"])).status_code == 200
    c, _ = await seed_conversation(partner)
    worker_login()
    base = f"/v1/partners/{partner}/incoming-leads"
    assert (await client.get(base)).json() == []
    response = await client.post(f"{base}/{c}/notes", json={"expected_revision": 0, "note": "Called lead; needs follow-up", "status": "handoff"})
    assert response.status_code == 200
    assert len((await client.get(base)).json()) == 1
    assert (await client.get(f"/v1/partners/{uuid.uuid4()}/incoming-leads/{c}")).status_code == 404
    assert (await client.post(f"{base}/{c}/notes", json={"expected_revision": 0, "note": "Stale note", "status": "closed"})).status_code == 409
    detail = (await client.get(f"{base}/{c}")).json()
    assert detail["source"] == "manual_tracking" and len(detail["events"]) == 2
    login(USER)
    assert (await client.delete(f"/v1/partners/{partner}/workers/{WORKER}")).status_code == 200
    worker_login()
    assert (await client.get(base)).status_code == 404
    assert (await client.get("/v1/partners")).json() == []


async def test_all_active_workers_notification_dedup_and_read_scope(client):
    partner = uuid.UUID((await create(client))["id"])
    invitation = await issue(client, partner)
    await join(client, invitation["token"])
    second = await issue(client, partner, "second@example.com")
    await join(client, second["token"], SECOND, "second@example.com")
    c, campaign = await seed_conversation(partner)
    worker_login()
    assert (await client.post("/v1/me/worker-devices", json={"token": "ab" * 32, "environment": "sandbox"})).status_code == 200
    login(USER)
    path = f"/v1/partners/{partner}/campaigns/{campaign}/conversations/{c}"
    assert (await client.patch(path, json={"expected_revision": 0, "status": "interested", "note": "Lead requested more information"})).status_code == 200
    worker_login()
    alerts = (await client.get("/v1/me/worker-notifications")).json()
    assert len(alerts) == 1 and alerts[0]["read"] is False
    worker_login(SECOND, "second@example.com")
    assert len((await client.get("/v1/me/worker-notifications")).json()) == 1
    assert (await client.post(f"/v1/me/worker-notifications/{alerts[0]['id']}/read")).status_code == 404
    async def duplicate(session):
        conversation = await session.get(Conversation, c)
        await notify_interested(session, partner, conversation, "new")
        await session.commit()
        assert len((await session.scalars(select(WorkerNotification))).all()) == 2
        assert len((await session.scalars(select(WorkerPushJob))).all()) == 1
    await database(duplicate)
    worker_login()
    assert (await client.post(f"/v1/me/worker-notifications/{alerts[0]['id']}/read")).status_code == 200
    assert (await client.get("/v1/me/worker-notifications")).json()[0]["read"] is True


def settings():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return SimpleNamespace(apns_key_id="test-key", apns_team_id="test-team", apns_topic="example.crewcrate",
        apns_private_key=SecretStr(pem.decode())), key


def test_apns_es256_signature():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    config, key = settings()
    token = provider_token(config)
    header, claims, signature = token.split(".")
    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    assert provider_token(config) == token
    assert json.loads(decode(header))["alg"] == "ES256"
    assert json.loads(decode(claims))["iss"] == "test-team"
    raw = decode(signature)
    key.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        (header + "." + claims).encode(), ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize("response_status,expected", [(200, "accepted"), (503, "pending"), (410, "failed")])
async def test_push_payload_retry_and_invalid_token(client, response_status, expected):
    partner = uuid.UUID((await create(client))["id"])
    invitation = await issue(client, partner)
    await join(client, invitation["token"])
    c, _ = await seed_conversation(partner)
    async def exercise(session):
        device = WorkerDevice(user_id=WORKER, token="ab" * 32, environment="sandbox")
        session.add(device)
        conversation = await session.get(Conversation, c)
        conversation.status = "handoff"
        conversation.revision += 1
        await session.flush()
        await notify_interested(session, partner, conversation, "new")
        await session.commit()
        calls = []
        def send(request):
            calls.append(request)
            return httpx.Response(response_status)
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as transport:
            config, _ = settings()
            assert await dispatch_batch(session, transport, config) == 1
        assert len(calls) == 1
        payload = json.loads(calls[0].content)
        assert payload["aps"]["sound"] == "default" and payload["aps"]["badge"] == 1
        assert calls[0].headers["apns-push-type"] == "alert"
        assert payload["conversation_id"] == str(c)
        job = await session.scalar(select(WorkerPushJob))
        assert job.status == expected
        assert device.active == (response_status != 410)
        if expected == "pending":
            assert aware(job.next_attempt_at) > now()
    await database(exercise)


async def test_push_cancelled_after_worker_removal_or_device_account_switch(client):
    partner = uuid.UUID((await create(client))["id"])
    invitation = await issue(client, partner)
    await join(client, invitation["token"])
    c, _ = await seed_conversation(partner)
    async def exercise(session):
        session.add(WorkerDevice(user_id=WORKER, token="ab" * 32, environment="sandbox"))
        conversation = await session.get(Conversation, c)
        conversation.status = "interested"
        await session.flush()
        await notify_interested(session, partner, conversation, "new")
        await session.commit()
        device = await session.scalar(select(WorkerDevice))
        device.user_id = OTHER
        await session.commit()
        def never_send(request):
            raise AssertionError("Must not send another user's push")
        async with httpx.AsyncClient(transport=httpx.MockTransport(never_send)) as transport:
            config, _ = settings()
            assert await dispatch_batch(session, transport, config) == 1
        assert (await session.scalar(select(WorkerPushJob))).status == "cancelled"
    await database(exercise)


async def test_disabled_push_and_removed_worker_cancel_pending_jobs(client):
    partner = uuid.UUID((await create(client))["id"])
    invitation = await issue(client, partner)
    await join(client, invitation["token"])
    c, _ = await seed_conversation(partner)
    async def exercise(session):
        from app.models import Member
        session.add(WorkerDevice(user_id=WORKER, token="ab" * 32, environment="sandbox"))
        conversation = await session.get(Conversation, c)
        conversation.status = "interested"
        await session.flush()
        await notify_interested(session, partner, conversation, "new")
        await session.commit()
        def never_send(request):
            raise AssertionError("Must not send while disabled or membership removed")
        async with httpx.AsyncClient(transport=httpx.MockTransport(never_send)) as transport:
            disabled = SimpleNamespace(apns_key_id="", apns_team_id="", apns_topic="", apns_private_key=None)
            assert await dispatch_batch(session, transport, disabled) == 0
            assert (await session.scalar(select(WorkerPushJob))).status == "pending"
            member = await session.get(Member, (partner, WORKER))
            member.active = False
            await session.commit()
            config, _ = settings()
            assert await dispatch_batch(session, transport, config) == 1
            assert (await session.scalar(select(WorkerPushJob))).status == "cancelled"
    await database(exercise)
    worker_login()
    assert (await client.get("/v1/me/worker-notifications")).json() == []
    assert (await client.post("/v1/me/worker-devices", json={"token": "ab" * 32, "environment": "sandbox"})).status_code == 403
