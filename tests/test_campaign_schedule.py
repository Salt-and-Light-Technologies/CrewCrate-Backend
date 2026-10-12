from datetime import datetime

import pytest
from pydantic import ValidationError
from test_api import client as client
from test_campaigns import config, ready_partner

from app.campaign_schedule import campaign_date_eligible
from app.schemas import CampaignConfig


@pytest.mark.parametrize("instant,expected", [
    ("2026-10-12T04:59:59+00:00", False),  # Sunday in Chicago
    ("2026-10-12T05:00:00+00:00", True),
    ("2026-10-19T05:00:00+00:00", True),
    ("2026-10-20T04:59:59+00:00", True),
    ("2026-10-20T05:00:00+00:00", False),
])
def test_inclusive_business_dates(instant, expected):
    plan = CampaignConfig(name="Week", start_date="2026-10-12", end_date="2026-10-19")
    assert campaign_date_eligible(plan, datetime.fromisoformat(instant)) is expected


@pytest.mark.parametrize("zone,instant,expected", [
    ("America/Chicago", "2026-11-01T05:00:00+00:00", True),
    ("America/Chicago", "2026-11-02T05:59:59+00:00", True),
    ("America/Chicago", "2026-11-02T06:00:00+00:00", False),
    ("Pacific/Auckland", "2026-10-31T11:00:00+00:00", True),
    ("Pacific/Auckland", "2026-11-01T11:00:00+00:00", False),
])
def test_timezone_and_dst_transitions(zone, instant, expected):
    plan = CampaignConfig(name="Day", time_zone=zone, start_date="2026-11-01", end_date="2026-11-01")
    assert campaign_date_eligible(plan, datetime.fromisoformat(instant)) is expected


@pytest.mark.parametrize("dates", [
    {"start_date": "2026-10-19", "end_date": "2026-10-12"},
    {"start_date": "2026-10-12"}, {"end_date": "2026-10-19"},
    {"start_date": "2026-02-30", "end_date": "2026-03-01"},
])
def test_invalid_ranges(dates):
    with pytest.raises(ValidationError):
        CampaignConfig(name="Bad", **dates)


def test_legacy_unrestricted_and_fail_closed_context():
    plan = CampaignConfig(name="Legacy")
    assert campaign_date_eligible(plan, datetime.fromisoformat("2026-10-12T00:00:00+00:00"))
    assert not campaign_date_eligible(plan, datetime(2026, 10, 12))
    plan.time_zone = "Missing/Zone"
    assert not campaign_date_eligible(plan, datetime.fromisoformat("2026-10-12T00:00:00+00:00"))


async def test_range_persistence_and_launch_gate(client):
    _, lead, url = await ready_partner(client)
    body = {**config(lead), "start_date": "2000-01-01", "end_date": "2000-01-08"}
    created = await client.post(url + "/campaigns", json=body)
    assert created.status_code == 201
    saved = created.json()
    path = url + "/campaigns/" + saved["id"]
    assert (await client.get(path)).json()["config"]["end_date"] == "2000-01-08"
    prepared = await client.post(path + "/prepare", json={"expected_revision": saved["revision"]})
    assert prepared.status_code == 200  # Planning remains available outside the sending dates.
    launched = await client.post(path + "/launch", json={"expected_revision": prepared.json()["revision"]})
    assert launched.status_code == 422 and "outside its selected dates" in launched.text
    assert (await client.get(path)).json()["revision"] == prepared.json()["revision"]
    body.update(start_date="2099-01-01", end_date="2099-01-08")
    edited = await client.put(path, json={"expected_revision": prepared.json()["revision"], "config": body})
    assert edited.status_code == 200 and edited.json()["config"]["start_date"] == "2099-01-01"
    prepared = await client.post(path + "/prepare", json={"expected_revision": edited.json()["revision"]})
    assert (await client.post(path + "/launch", json={"expected_revision": prepared.json()["revision"]})).status_code == 422
    body.update(start_date=None, end_date=None)
    edited = await client.put(path, json={"expected_revision": prepared.json()["revision"], "config": body})
    assert edited.status_code == 200 and edited.json()["config"]["start_date"] is None
