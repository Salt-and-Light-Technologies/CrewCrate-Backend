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
    assert response.status_code == 503
    generated = (await client.get(path)).json()["config"]["message_template"]
    assert "Reply STOP to opt out" in generated and generated != value["message_template"]


async def test_ai_generates_opening_and_failure_preserves_draft(client):
    from fastapi import HTTPException

    from app.ai_tests import AgentDecision
    from app.campaigns import get_opening_agent
    from app.main import app

    _, lead, url = await ready_partner(client)
    value = config(lead)
    value["message_template"] = ""
    value["ai_name"] = "Maddie"
    value["ai_brief"] = "Ask about their guitar interests"
    campaign = (await client.post(url + "/campaigns", json=value)).json()
    path = url + "/campaigns/" + campaign["id"]
    contexts = []

    class Agent:
        invalid = True
        unavailable = False

        async def generate(self, context, messages):
            contexts.append(context)
            assert messages == []
            if self.unavailable:
                raise HTTPException(502, "AI unavailable")
            text = (
                "Wrong opening"
                if self.invalid
                else f"Hi, I'm Maddie from {context['business_name']}. {context['offer']} Reply STOP to opt out."
            )
            return AgentDecision(reply=text, decision="continue", reason="Opening", handoff_summary="")

    agent = Agent()
    app.dependency_overrides[get_opening_agent] = lambda: agent
    assert (await client.post(path + "/prepare", json=dict(expected_revision=1))).status_code == 502
    current = (await client.get(path)).json()
    assert current["revision"] == 1 and current["config"]["message_template"] == ""
    agent.unavailable = True
    assert (await client.post(path + "/prepare", json=dict(expected_revision=1))).status_code == 502
    agent.unavailable = False
    agent.invalid = False
    prepared = await client.post(path + "/prepare", json=dict(expected_revision=1))
    assert prepared.status_code == 200
    assert prepared.json()["config"]["message_template"].startswith("Hi, I'm Maddie")
    assert contexts[-1]["ai_instructions"] == value["ai_brief"]
    assert contexts[-1]["qualification"] == value["qualification"]
    assert contexts[-1]["offer"] == value["offer"]
    assert (await client.get(path + "/conversations")).json() == []


async def test_eligibility_filtered_before_pagination(client):
    _, lead, url = await ready_partner(client)
    campaign = (await client.post(url + '/campaigns', json=config(lead))).json()
    allowed = (await client.get(url + '/leads', params=dict(eligibility='eligible', limit=250))).json()
    excluded = (await client.get(url + '/leads', params=dict(eligibility='excluded', limit=250))).json()
    assert len(allowed) == 1 and allowed[0]['id'] == lead['id']
    assert len(excluded) == 1 and not excluded[0]['eligible_for_sms']
    assert (await client.get(url + '/leads', params=dict(campaign_id=campaign['id'], eligibility='excluded'))).json() == []
    assert (await client.get(url + '/leads', params=dict(eligibility='eligible', limit=1, offset=1))).json() == []
    assert (await client.get(url + '/leads', params=dict(limit=251))).status_code == 422


def test_ten_thousand_campaign_capacity():
    from app.schemas import CampaignConfig, RecipientSelection
    ids = [uuid.uuid4() for _ in range(10000)]
    assert len(CampaignConfig(name='Large list', lead_ids=ids).lead_ids) == 10000
    assert len(RecipientSelection(expected_revision=1, lead_ids=ids).lead_ids) == 10000


async def test_load_more_pages_and_full_list_filter(client):
    from sqlalchemy import update
    from test_api import create, upload

    from app.database import get_session
    from app.main import app
    from app.models import Lead

    partner = await create(client)
    pid = partner['id']
    raw = ('Phone,Name\n' + '\n'.join(f'{15550000000+i},Contact {i}' for i in range(751))).encode()
    assert (await upload(client, pid, 0, raw)).status_code == 201
    async for session in app.dependency_overrides[get_session]():
        await session.execute(update(Lead).where(Lead.partner_id == uuid.UUID(pid), Lead.phone >= '15550000500').values(status='eligible', sms_permission='recorded', permission_evidence='Signed opt-in reference'))
        await session.commit()
    path = '/v1/partners/' + pid + '/leads'
    pages = [(await client.get(path, params=dict(limit=250, offset=offset))).json() for offset in [0, 250, 500, 750]]
    assert [len(page) for page in pages] == [250, 250, 250, 1]
    assert len({item['id'] for page in pages for item in page}) == 751
    first = (await client.get(path, params=dict(limit=250, eligibility='eligible'))).json()
    last = (await client.get(path, params=dict(limit=250, eligibility='eligible', offset=250))).json()
    assert len(first) == 250 and len(last) == 1
    assert all(item['eligible_for_sms'] for item in first + last)
    all_ids = (await client.get('/v1/partners/' + pid + '/eligible-recipient-ids')).json()
    assert len(all_ids) == 251
    assert set(all_ids) == {item['id'] for item in first + last}
    assert (await client.get('/v1/partners/' + pid + '/eligible-recipient-ids', params={'search': 'Contact 750'})).json() == [next(item['id'] for item in first + last if item['name'] == 'Contact 750')]
