import uuid

from test_api import client as client
from test_campaigns import config, ready_partner


async def test_review_requires_evidence_and_preserves_optout(client):
    _, lead, url = await ready_partner(client)
    contacts = (await client.get(url + "/leads")).json()
    unknown = next(x for x in contacts if x["id"] != lead["id"])
    assert not unknown["eligible_for_sms"] and unknown["exclusion_reasons"]
    path = url + "/leads/" + unknown["id"] + "/contact-review"
    body = dict(
        expected_revision=0,
        action="record_permission",
        evidence="Signed SMS consent record 123",
        source="Website consent form",
        consent_date="2020-01-01",
        country="US",
        attested=False,
    )
    assert (await client.post(path, json=body)).status_code == 422
    body["attested"] = True
    body["consent_date"] = "2099-01-01"
    assert (await client.post(path, json=body)).status_code == 422
    body["consent_date"] = "2020-01-01"
    reviewed = await client.post(path, json=body)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["eligible_for_sms"]
    assert "Website consent form" in reviewed.json()["permission_evidence"]
    assert (await client.post(path, json=body)).status_code == 409
    excluded = await client.post(
        path, json=dict(expected_revision=1, action="exclude", evidence="Not in this campaign service area")
    )
    assert excluded.status_code == 200 and not excluded.json()["eligible_for_sms"]
    stopped = await client.post(
        path, json=dict(expected_revision=2, action="opt_out", evidence="Contact requested no more messages")
    )
    assert stopped.status_code == 200 and stopped.json()["opted_out"]
    body["expected_revision"] = 3
    assert (await client.post(path, json=body)).status_code == 409


async def test_selection_filters_unknown_and_rechecks_at_start(client):
    _, lead, url = await ready_partner(client)
    contacts = (await client.get(url + "/leads")).json()
    campaign = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + campaign["id"]
    selected = await client.put(
        path + "/recipients", json=dict(expected_revision=1, lead_ids=[x["id"] for x in contacts])
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["removed_count"] == 1
    assert selected.json()["campaign"]["config"]["lead_ids"] == [lead["id"]]
    assert (
        await client.put(path + "/recipients", json=dict(expected_revision=1, lead_ids=[]))
    ).status_code == 409
    assert (
        await client.put(path + "/recipients", json=dict(expected_revision=2, lead_ids=[str(uuid.uuid4())]))
    ).status_code == 422
    prepared = await client.post(path + "/prepare", json=dict(expected_revision=2))
    assert prepared.status_code == 200 and prepared.json()["revision"] == 3
    launch = await client.post(path + "/launch", json=dict(expected_revision=3))
    assert launch.status_code == 503 and "Nothing was queued or sent" in launch.text
    assert (await client.get(path + "/conversations")).json() == []
    review = await client.post(
        url + "/leads/" + lead["id"] + "/contact-review",
        json=dict(expected_revision=2, action="opt_out", evidence="STOP received during preparation"),
    )
    assert review.status_code == 200
    blocked = await client.post(path + "/launch", json=dict(expected_revision=3))
    assert blocked.status_code == 422 and "blockers" in blocked.json()["detail"]
    cleared = await client.put(path + "/recipients", json=dict(expected_revision=3, lead_ids=[lead["id"]]))
    assert cleared.json()["removed_count"] == 1 and cleared.json()["campaign"]["config"]["lead_ids"] == []


async def test_review_and_selection_respect_tenant_and_hold(client):
    from test_api import OTHER, OWNER, USER, create, login

    _, lead, url = await ready_partner(client)
    campaign = (await client.post(url + "/campaigns", json=config(lead))).json()
    path = url + "/campaigns/" + campaign["id"]
    login(OWNER)
    other = await create(client, OTHER)
    login(USER)
    other_url = "/v1/partners/" + other["id"]
    assert (
        await client.post(
            other_url + "/leads/" + lead["id"] + "/contact-review",
            json=dict(expected_revision=2, action="exclude", evidence="Attempted foreign exclusion"),
        )
    ).status_code == 404
    assert (
        await client.put(
            other_url + "/campaigns/" + campaign["id"] + "/recipients",
            json=dict(expected_revision=1, lead_ids=[lead["id"]]),
        )
    ).status_code == 404
    login(OWNER)
    assert (
        await client.post(
            path + "/control", json=dict(expected_revision=1, action="pause", reason="Owner hold for review")
        )
    ).status_code == 200
    login(USER)
    assert (
        await client.put(path + "/recipients", json=dict(expected_revision=2, lead_ids=[]))
    ).status_code == 409


async def test_opening_sms_requires_explicit_stop_instruction(client):
    _, lead, url = await ready_partner(client)
    value = config(lead)
    value["message_template"] = "Stop by our store for a discount"
    campaign = (await client.post(url + "/campaigns", json=value)).json()
    path = url + "/campaigns/" + campaign["id"]
    assert (await client.post(path + "/prepare", json=dict(expected_revision=1))).status_code == 200
    response = await client.post(path + "/launch", json=dict(expected_revision=2))
    assert response.status_code == 422 and "Reply STOP" in response.text
