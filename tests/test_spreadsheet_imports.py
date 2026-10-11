import io
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException
from test_api import OTHER, USER, client, create, login  # noqa: F401

from app.spreadsheet_imports import decode_sheet, parse_file


def excel_file(formula=False):
    """Minimal real OOXML fixture with two worksheets; no external workbook tooling."""
    output = io.BytesIO()
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    sheet = f'''<worksheet xmlns="{ns}"><dimension ref="A1:C3"/><sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>Phone</t></is></c><c r="B1" t="inlineStr"><is><t>Name</t></is></c><c r="C1" t="inlineStr"><is><t>Email</t></is></c></row>
    <row r="2"><c r="A2"><v>15551234567</v></c><c r="B2" t="inlineStr"><is><t>Jamie</t></is></c><c r="C2" t="inlineStr"><is><t>jamie@example.com</t></is></c></row>
    <row r="3"><c r="A3" t="inlineStr"><is><t>invalid</t></is></c></row>
    </sheetData></worksheet>'''
    if formula:
        sheet = sheet.replace('<v>15551234567</v>', '<f>1+1</f><v>2</v>')
    with ZipFile(output, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>''')
        z.writestr('_rels/.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml', f'''<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Leads" sheetId="1" r:id="rId1"/><sheet name="Other" sheetId="2" r:id="rId2"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>''')
        z.writestr('xl/worksheets/sheet1.xml', sheet)
        z.writestr('xl/worksheets/sheet2.xml', sheet.replace('Jamie', 'Other Person'))
    return output.getvalue()


@pytest.mark.parametrize('filename,raw', [('leads.csv', b'Phone,Name\n15551234567,Jamie\n'), ('leads.tsv', b'Phone\tName\n15551234567\tJamie\n'), ('leads.csv', b'Phone;Name\n15551234567;Jamie\n'), ('leads.xlsx', excel_file())])
def test_supported_formats(filename, raw):
    parsed = parse_file(raw, filename, None, 'Phone', 'Name', None)
    assert parsed.contacts[0]['phone'] == '15551234567'
    assert parsed.contacts[0]['name'] == 'Jamie'


@pytest.mark.parametrize('raw,filename', [(b'junk', 'leads.xlsx'), (b'junk', 'leads.xls'), (b'Phone,Phone\n1,2\n', 'leads.csv'), (excel_file(True), 'leads.xlsx'), (b'a' * 5_000_001, 'leads.csv')])
def test_bad_files_rejected(raw, filename):
    with pytest.raises(HTTPException):
        decode_sheet(raw, filename)


async def test_campaign_file_save_and_edit(client):  # noqa: F811
    partner = await create(client)
    login(USER)
    path = f"/v1/partners/{partner['id']}"
    columns = await client.post(path + '/imports/columns', files={'file': ('leads.xlsx', excel_file())})
    assert columns.status_code == 200, columns.text
    assert columns.json()['sheets'] == ['Leads', 'Other']
    assert columns.json()['columns'] == ['Phone', 'Name', 'Email']
    other = await client.post(path + '/imports/columns', files={'file': ('leads.xlsx', excel_file())}, data={'sheet': 'Other'})
    assert other.json()['selected_sheet'] == 'Other'
    preview = await client.post(path + '/imports/preview', files={'file': ('leads.xlsx', excel_file())}, data={'phone_column': 'Phone', 'name_column': 'Name', 'email_column': 'Email'})
    assert preview.status_code == 200
    assert preview.json()['imported'] == 1 and preview.json()['invalid'] == 1
    fields = {'config': json.dumps({'name': 'Campaign', 'offer': 'Discount'}), 'phone_column': 'Phone', 'name_column': 'Name', 'email_column': 'Email', 'expected_partner_revision': str(preview.json()['partner_revision'])}
    result = await client.post(path + '/campaign-import', files={'file': ('leads.xlsx', excel_file())}, data=fields)
    assert result.status_code == 200, result.text
    campaign = result.json()
    assert len(campaign['config']['lead_ids']) == 1
    lead = (await client.get(path + '/leads')).json()[0]
    assert lead['sms_permission'] == 'unknown' and not lead['opted_out']
    # Existing suppression survives reupload; importing never recreates consent.
    import uuid

    from app.database import get_session
    from app.main import app
    from app.models import Lead
    async for session in app.dependency_overrides[get_session]():
        stored = await session.get(Lead, uuid.UUID(lead['id']))
        stored.opted_out = True
        stored.permission_evidence = 'Existing suppression evidence'
        await session.commit()
    updated = {**fields, 'campaign_id': campaign['id'], 'expected_revision': str(campaign['revision']), 'expected_partner_revision': str(preview.json()['partner_revision'] + 1), 'config': json.dumps({**campaign['config'], 'offer': 'Updated'})}
    result = await client.post(path + '/campaign-import', files={'file': ('leads.xlsx', excel_file())}, data=updated)
    assert result.status_code == 200, result.text
    assert result.json()['config']['lead_ids'] == campaign['config']['lead_ids']
    assert result.json()['config']['offer'] == 'Updated'
    assert result.json()['status'] == 'draft'
    leads = (await client.get(path + '/leads')).json()
    assert len(leads) == 1
    assert leads[0]['opted_out'] and leads[0]['permission_evidence'] == 'Existing suppression evidence'
    assert (await client.post(path + '/campaign-import', files={'file': ('leads.xlsx', excel_file())}, data=updated)).status_code == 409
    login(OTHER)
    assert (await client.post(path + '/imports/columns', files={'file': ('leads.xlsx', excel_file())})).status_code == 404
    assert (await client.post(path + '/campaign-import', files={'file': ('leads.xlsx', excel_file())}, data=updated)).status_code == 404


async def test_oversized_campaign_rolls_back(client):  # noqa: F811
    partner = await create(client)
    login(USER)
    path = f"/v1/partners/{partner['id']}"
    raw = ('Phone\n' + '\n'.join(str(15550000000 + i) for i in range(10001))).encode()
    result = await client.post(path + '/campaign-import', files={'file': ('leads.csv', raw)}, data={'config': '{"name":"Too many"}', 'phone_column': 'Phone', 'expected_partner_revision': '0'})
    assert result.status_code == 422
    assert (await client.get(path + '/leads')).json() == []
    assert (await client.get(path + '/campaigns')).json() == []
    assert (await client.get(path + '/imports')).json() == []


async def test_campaign_permission_columns_and_duplicate_optouts(client):  # noqa: F811
    import json
    partner = await create(client)
    login(USER)
    path = f"/v1/partners/{partner['id']}"
    mapping = json.dumps(dict(permission='Permission', evidence='Evidence', optout='Optout', test='Test'))
    raw = b'Phone,Permission,Evidence,Optout,Test\n12025550101,recorded,SIMULATED consent record,false,true\n12025550102,unknown,,false,true\n12025550103,revoked,Asked to stop,true,true\n'
    result = await client.post(path + '/campaign-import', files={'file': ('leads.csv', raw)}, data={'config': '{"name":"Permissions"}', 'phone_column':'Phone', 'permission_columns':mapping, 'expected_partner_revision':'0'})
    assert result.status_code == 200, result.text
    leads = (await client.get(path + '/leads')).json()
    byphone = {lead['phone']:lead for lead in leads}
    assert byphone['12025550101']['eligible_for_sms']
    assert not byphone['12025550102']['eligible_for_sms']
    assert byphone['12025550103']['opted_out']
    assert any('Test-only' in b for b in result.json()['blockers'])
    ids = (await client.get(path + '/eligible-recipient-ids')).json()
    assert ids == [byphone['12025550101']['id']]
    current = (await client.get(path)).json()
    retry = b'Phone,Permission,Evidence,Optout,Test\n12025550103,recorded,SIMULATED new consent,false,true\n'
    result2 = await client.post(path + '/campaign-import', files={'file':('retry.csv', retry)}, data={'config':'{"name":"Retry"}', 'phone_column':'Phone','permission_columns':mapping,'expected_partner_revision':str(current['revision'])})
    assert result2.status_code == 200, result2.text
    stopped = next(x for x in (await client.get(path+'/leads')).json() if x['phone']=='12025550103')
    assert stopped['opted_out'] and not stopped['eligible_for_sms']


def test_real_permission_requires_attestation_and_evidence():
    from app.imports import parse_csv
    raw = b'Phone,Permission,Evidence\n12025550101,recorded,Signed form reference ABC\n'
    mapping = '{"permission":"Permission","evidence":"Evidence"}'
    assert parse_csv(raw,'Phone',None,None,mapping).contacts[0]['sms_permission'] == 'unknown'
    assert parse_csv(raw,'Phone',None,None,mapping,True).contacts[0]['sms_permission'] == 'recorded'
