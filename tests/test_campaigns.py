import pytest
from app.messaging import DisabledMessagingProvider, MessagingUnavailable
from test_api import OTHER, OWNER, USER, complete, create, login, upload
from test_api import client as client


async def ready_partner(client):
    p = await create(client)
    pid = p["id"]
    url = "/v1/partners/" + pid
    assert (
        await client.put(url + "/onboarding", json={"expected_revision": 0, "onboarding": complete()})
    ).status_code == 200
    assert (
        await upload(client, pid, 1, b"Phone,Name\n12025550101,First\n12025550102,Second\n")
    ).status_code == 201
    assert (await client.post(url + "/submit", json={"expected_revision": 2})).status_code == 200
    assert (
        await client.put(
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
    ).status_code == 200
    assert (
        await client.post(url + "/decisions", json={"expected_revision": 4, "action": "approve"})
    ).status_code == 200
    login(USER)
    lead = (await client.get(url + "/leads")).json()[0]
    assert (
        await client.patch(url + "/leads/" + lead["id"], json={"expected_revision": 0, "status": "eligible"})
    ).status_code == 200
    assert (
        await client.put(
            url + "/leads/" + lead["id"] + "/sms-permission",
            json={
                "expected_revision": 1,
                "permission": "recorded",
                "evidence": "Permission reference ABC123",
            },
        )
    ).status_code == 200
    return p, lead, url


def config(lead):
    return dict(
        name="Recovery plan",
        offer="Reconnect about your enquiry",
        message_template="Would you like to talk to our team? Reply STOP to opt out.",
        qualification="Interested and in service area",
        handoff_email="sales@example.com",
        time_zone="America/Chicago",
        lead_ids=[lead["id"]],
    )


async def test_partner_self_service_and_disabled_delivery(client):
    p, lead, url = await ready_partner(client)
    created = await client.post(url + "/campaigns", json=config(lead))
    assert created.status_code == 201, created.text
    campaign = created.json()
    path = url + "/campaigns/" + campaign["id"]
    prepared = await client.post(path + "/prepare", json={"expected_revision": 1})
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["status"] == "ready_to_connect"
    assert prepared.json()["blockers"] == [] and prepared.json()["recipient_count"] == 1
    assert not prepared.json()["sending_enabled"] and not prepared.json()["messaging_connected"]
    assert (await client.post(path + "/launch", json={"expected_revision": 2})).status_code == 503
    assert (await client.get(path)).json()["revision"] == 2
    history = (await client.get(path + "/history")).json()
    assert len(history) == 2 and all(e["actor_id"] == str(USER) for e in history)
    assert (await client.get("/v1/messaging/capabilities")).json() == dict(
        provider_connected=False, outbound_enabled=False, inbound_enabled=False
    )
    login(OWNER)
    assert len((await client.get("/v1/owner/campaigns")).json()) == 1
    assert (
        await client.post(
            path + "/control",
            json={"expected_revision": 2, "action": "pause", "reason": "Owner paused the plan"},
        )
    ).json()["status"] == "paused"
    login(USER)
    assert (await client.post(path + "/prepare", json={"expected_revision": 3})).status_code == 409
    assert (
        await client.post(
            path + "/control",
            json={"expected_revision": 3, "action": "resume", "reason": "Partner cannot bypass owner hold"},
        )
    ).status_code == 403
    login(OWNER)
    resumed = await client.post(
        path + "/control", json={"expected_revision": 3, "action": "resume", "reason": "Resume planning"}
    )
    assert resumed.json()["status"] == "draft"
    assert (await client.post(path + "/prepare", json={"expected_revision": 3})).status_code == 409


async def test_permissions_optouts_and_preparation_invalidation(client):
    p, lead, url = await ready_partner(client)
    created = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + created["id"]
    assert (await client.post(path + "/prepare", json={"expected_revision": 1})).status_code == 200
    revoked = await client.put(
        url + "/leads/" + lead["id"] + "/sms-permission",
        json={"expected_revision": 2, "permission": "revoked", "evidence": "Recorded opt-out request"},
    )
    assert revoked.status_code == 200 and revoked.json()["opted_out"]
    snapshot = (await client.get(path)).json()
    assert snapshot["needs_recheck"] and snapshot["recipient_count"] == 0
    assert (await client.post(path + "/prepare", json={"expected_revision": 2})).status_code == 422
    assert (
        await client.put(
            url + "/leads/" + lead["id"] + "/sms-permission",
            json={"expected_revision": 3, "permission": "recorded", "evidence": "Attempted reactivation"},
        )
    ).status_code == 409
    events = (await client.get(url + "/lead-activity")).json()
    assert any("Permission reference ABC123" in e["reason"] for e in events)
    assert any("Recorded opt-out request" in e["reason"] for e in events)


async def test_campaign_tenant_scope_limits_and_missing_checks(client):
    p, lead, url = await ready_partner(client)
    c = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + c["id"]
    assert (await client.get("/v1/owner/campaigns")).status_code == 403
    login(OWNER)
    other = await create(client, OTHER)
    login(USER)
    otherpath = "/v1/partners/" + other["id"] + "/campaigns/" + c["id"]
    for suffix in ("", "/history", "/conversations"):
        assert (await client.get(otherpath + suffix)).status_code == 404
    assert (await client.post(otherpath + "/prepare", json={"expected_revision": 1})).status_code == 404
    assert (
        await client.put(
            "/v1/partners/" + other["id"] + "/leads/" + lead["id"] + "/sms-permission",
            json={"expected_revision": 2, "permission": "recorded", "evidence": "Bad partner evidence"},
        )
    ).status_code == 404
    too_many = config(lead)
    too_many["daily_limit"] = 1000
    assert (await client.post(url + "/campaigns", json=too_many)).status_code == 201
    too_many["daily_limit"] = 0
    assert (await client.post(url + "/campaigns", json=too_many)).status_code == 422
    invalid = config(lead)
    invalid["start_hour"] = 16
    invalid["end_hour"] = 10
    saved = (await client.put(path, json={"expected_revision": 1, "config": invalid})).json()
    assert saved["status"] == "draft" and saved["blockers"]
    assert (await client.post(path + "/prepare", json={"expected_revision": 2})).status_code == 422
    assert (await client.put(path, json={"expected_revision": 1, "config": config(lead)})).status_code == 409


async def test_manual_tracking_and_note_history(client):
    p, lead, url = await ready_partner(client)
    c = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + c["id"]
    tracked = await client.post(
        path + "/conversations", json={"lead_id": lead["id"], "note": "Internal follow-up record"}
    )
    assert tracked.status_code == 201, tracked.text
    value = tracked.json()
    assert value["source"] == "manual_tracking"
    assert (
        await client.post(
            path + "/conversations", json={"lead_id": lead["id"], "note": "Duplicate internal record"}
        )
    ).status_code == 409
    conversationpath = path + "/conversations/" + value["id"]
    updated = await client.patch(
        conversationpath,
        json={"expected_revision": 0, "status": "handoff", "note": "Team will follow up manually"},
    )
    assert updated.status_code == 200 and updated.json()["revision"] == 1
    assert (
        await client.patch(
            conversationpath,
            json={"expected_revision": 0, "status": "closed", "note": "Stale update should fail"},
        )
    ).status_code == 409
    history = (await client.get(conversationpath + "/history")).json()
    assert [x["revision"] for x in history] == [1, 0]
    assert all(x["actor_id"] == str(USER) for x in history)
    assert len((await client.get(path + "/conversations")).json()) == 1
    current = (await client.get(path)).json()
    assert (
        await client.post(
            path + "/control",
            json={
                "expected_revision": current["revision"],
                "action": "archive",
                "reason": "Pilot planning complete",
            },
        )
    ).json()["status"] == "archived"
    assert (
        await client.patch(
            conversationpath,
            json={"expected_revision": 1, "status": "closed", "note": "Archived update blocked"},
        )
    ).status_code == 409


async def test_disabled_provider_never_succeeds():
    with pytest.raises(MessagingUnavailable):
        await DisabledMessagingProvider().send_sms(
            recipient="12025550101", body="Draft", idempotency_key="test"
        )


async def test_partner_can_resume_own_pause_and_cannot_bypass_owner_hold(client):
    p, lead, url = await ready_partner(client)
    campaign = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + campaign["id"]
    paused = await client.post(
        path + "/control",
        json={"expected_revision": 1, "action": "pause", "reason": "Partner paused planning"},
    )
    assert paused.status_code == 200 and not paused.json()["owner_hold"]
    resumed = await client.post(
        path + "/control",
        json={"expected_revision": 2, "action": "resume", "reason": "Partner resumed planning"},
    )
    assert resumed.status_code == 200 and resumed.json()["status"] == "draft"
    login(OWNER)
    held = await client.post(
        path + "/control",
        json={"expected_revision": 3, "action": "pause", "reason": "Owner inspection needed"},
    )
    assert held.json()["owner_hold"]
    login(USER)
    assert (await client.put(path, json={"expected_revision": 4, "config": config(lead)})).status_code == 409
    assert (
        await client.post(
            path + "/control",
            json={"expected_revision": 4, "action": "archive", "reason": "Attempted bypass archive"},
        )
    ).status_code == 403
    assert (await client.get(path)).json()["owner_hold"]
