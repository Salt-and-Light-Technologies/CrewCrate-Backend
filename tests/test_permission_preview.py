import json

import pytest
from sqlalchemy import select
from test_api import USER, create, login
from test_api import client as client

from app.models import LeadImport

MAPPING = json.dumps(dict(permission="SMS permission", evidence="Permission evidence", optout="Opted out"))
RAW = (b"Phone,Name,SMS permission,Permission evidence,Opted out,Notes\n"
       b"12025550101,Allowed,yes,Signed consent ABC,false,Roof repair\n"
       b"12025550102,Stopped,yes,Signed consent DEF,true,Do not call\n"
       b"12025550103,Unknown,unknown,,false,No consent\n"
       b"12025550104,Blank,,,false,Missing consent\n"
       b"12025550105,NoEvidence,yes,,false,No evidence\n")


def fields(**extra):
    return dict(phone_column="Phone", name_column="Name", permission_columns=MAPPING,
                permission_attested="true", **extra)


async def test_mixed_preview_import_parity_and_sticky_optout(client):
    partner = await create(client)
    login(USER)
    path = f"/v1/partners/{partner['id']}"
    response = await client.post(path + "/imports/preview", files={"file": ("leads.csv", RAW)}, data=fields())
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["eligible_count"] == 1 and preview["excluded_count"] == 4
    assert len(preview["contacts"]) == 5
    assert all(row["exclusion_reasons"] for row in preview["contacts"] if not row["eligible_for_sms"])
    assert (await client.get(path + "/leads")).json() == []  # Read-only preview.
    imported = await client.post(path + "/campaign-import", files={"file": ("leads.csv", RAW)},
                                 data=fields(config='{"name":"Mixed"}', expected_partner_revision="0", eligible_recipients_only="true"))
    assert imported.status_code == 200, imported.text
    leads = (await client.get(path + "/leads")).json()
    assert len(leads) == 5  # Excluded contacts are retained, not dropped.
    byphone = {lead["phone"]: lead for lead in leads}
    for row in preview["contacts"]:
        assert byphone[row["phone"]]["eligible_for_sms"] == row["eligible_for_sms"]
        assert byphone[row["phone"]]["opted_out"] == row["opted_out"]
    campaign = imported.json()
    assert campaign["config"]["lead_ids"] == [byphone["12025550101"]["id"]]
    chosen = await client.put(path + "/campaigns/" + campaign["id"] + "/recipients", json={
        "expected_revision": campaign["revision"], "lead_ids": [lead["id"] for lead in leads],
    })
    assert chosen.status_code == 200
    assert chosen.json()["campaign"]["config"]["lead_ids"] == [byphone["12025550101"]["id"]]
    retry = b"Phone,Name,SMS permission,Permission evidence,Opted out\n12025550102,Stopped,yes,New consent ABC,false\n"
    predicted = await client.post(path + "/imports/preview", files={"file": ("retry.csv", retry)}, data=fields())
    assert predicted.json()["eligible_count"] == 0 and predicted.json()["contacts"][0]["opted_out"]
    reimported = await client.post(path + "/campaign-import", files={"file": ("retry.csv", retry)}, data=fields(
        config='{"name":"Retry"}', expected_partner_revision=str(predicted.json()["partner_revision"])))
    assert reimported.status_code == 200
    stopped = next(lead for lead in (await client.get(path + "/leads")).json() if lead["phone"] == "12025550102")
    assert stopped["opted_out"] and not stopped["eligible_for_sms"]
    # Verify source values are retained for audit, including an excluded contact's notes.
    from app.database import get_session
    from app.main import app
    async for session in app.dependency_overrides[get_session]():
        batches = (await session.scalars(select(LeadImport))).all()
        assert batches[0].raw_rows[1]["values"]["Notes"] == "Do not call"
        assert batches[0].raw_rows[1]["values"]["Opted out"] == "true"


@pytest.mark.parametrize("permission,optout", [("maybe", "false"), ("yes", "maybe"), ("1", "false")])
async def test_unknown_permission_values_fail_closed(client, permission, optout):
    partner = await create(client)
    login(USER)
    path = f"/v1/partners/{partner['id']}"
    raw = f"Phone,SMS permission,Permission evidence,Opted out\n12025550101,{permission},Signed consent ABC,{optout}\n".encode()
    data = dict(phone_column="Phone", permission_columns=MAPPING, permission_attested="true")
    preview = await client.post(path + "/imports/preview", files={"file": ("bad.csv", raw)}, data=data)
    assert preview.status_code == 422
    uploaded = await client.post(path + "/campaign-import", files={"file": ("bad.csv", raw)}, data={
        **data, "config": '{"name":"Bad"}', "expected_partner_revision": "0",
    })
    assert uploaded.status_code == 422
    assert (await client.get(path + "/leads")).json() == []


async def test_unmapped_columns_and_missing_attestation_are_not_consent(client):
    partner = await create(client)
    login(USER)
    path = f"/v1/partners/{partner['id']}"
    for data in [dict(phone_column="Phone"), dict(phone_column="Phone", permission_columns=MAPPING)]:
        preview = await client.post(path + "/imports/preview", files={"file": ("leads.csv", RAW)}, data=data)
        assert preview.status_code == 200
        assert preview.json()["eligible_count"] == 0 and preview.json()["excluded_count"] == 5


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_rows_optout_wins_in_any_order(reverse):
    from app.imports import parse_csv, preview_eligibility
    rows = ["12025550101,yes,Signed consent ABC,false", "12025550101,yes,Stop request ABC,true"]
    if reverse:
        rows.reverse()
    raw = ("Phone,SMS permission,Permission evidence,Opted out\n" + "\n".join(rows) + "\n").encode()
    parsed = parse_csv(raw, "Phone", None, None, MAPPING, True)
    preview = preview_eligibility(parsed, {})
    assert preview["eligible_count"] == 0 and preview["excluded_count"] == 1
    assert preview["contacts"][0]["opted_out"]
    assert len(parsed.raw_rows) == 2


def test_excel_permission_mapping_matches_csv():
    import io

    from openpyxl import Workbook

    from app.spreadsheet_imports import parse_file
    csv_result = parse_file(RAW, "leads.csv", None, "Phone", "Name", None, MAPPING, True)
    workbook = Workbook()
    for row in RAW.decode().splitlines():
        workbook.active.append(row.split(","))
    output = io.BytesIO()
    workbook.save(output)
    result = parse_file(output.getvalue(), "leads.xlsx", None, "Phone", "Name", None, MAPPING, True)
    assert result.contacts == csv_result.contacts
    assert result.raw_rows == csv_result.raw_rows
