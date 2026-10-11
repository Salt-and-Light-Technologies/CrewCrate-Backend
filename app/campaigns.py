import re
import uuid
from datetime import date, datetime, timezone
from pathlib import PurePath
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from .auth import Actor, accessible_partner, current_actor, is_owner, require_owner
from .contact_policy import exclusion_reasons
from .database import get_session
from .imports import MAX_BYTES, preview_rows
from .models import Campaign, CampaignEvent, Conversation, ConversationEvent, Lead, LeadImport, Partner
from .schemas import (
    CampaignConfig,
    CampaignControl,
    CampaignEventRead,
    CampaignRead,
    CampaignUpdate,
    ContactReview,
    ConversationCreate,
    ConversationEventRead,
    ConversationRead,
    ConversationUpdate,
    LeadRead,
    PermissionUpdate,
    RecipientSelection,
    RecipientSelectionRead,
    RevisionRequest,
)
from .spreadsheet_imports import parse_file
from .workflow import email_valid, launch_issues, record_event

router = APIRouter(prefix="/v1")
Session = Annotated[AsyncSession, Depends(get_session)]
User = Annotated[Actor, Depends(current_actor)]
OwnerUser = Annotated[Actor, Depends(require_owner)]


async def get_campaign(partner_id, campaign_id, actor, session, lock=False):
    partner = await accessible_partner(partner_id, actor, session, lock=lock)
    query = select(Campaign).where(Campaign.id == campaign_id, Campaign.partner_id == partner_id)
    if lock:
        query = query.with_for_update()
    campaign = await session.scalar(query.execution_options(populate_existing=True))
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    return partner, campaign


def revision(record, expected):
    if record.revision != expected:
        raise HTTPException(409, "Record changed; refresh before saving")


def event(campaign, actor, action, detail, session):
    campaign.revision += 1
    campaign.updated_at = datetime.now(timezone.utc)
    session.add(
        CampaignEvent(
            campaign_id=campaign.id,
            actor_id=actor.user_id,
            action=action,
            detail=detail,
            revision=campaign.revision,
            timestamp=campaign.updated_at,
        )
    )


async def campaign_read(partner, campaign, session):
    config = CampaignConfig.model_validate(campaign.config)
    all_count = await session.scalar(
        select(func.count()).select_from(Lead).where(Lead.partner_id == partner.id)
    )
    blockers = launch_issues(partner, all_count)
    if partner.status != "pilot_approved":
        blockers.append("Partner setup must be pilot approved")
    for field in ("offer", "qualification"):
        if not getattr(config, field).strip():
            blockers.append("Complete " + field.replace("_", " "))
    if not email_valid(config.handoff_email):
        blockers.append("Provide a valid sales handoff email")
    try:
        ZoneInfo(config.time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        blockers.append("Provide a valid IANA time zone")
    if config.end_hour == 17 and (config.end_minute or 0) > 0:
        blockers.append("Sending window must end by 17:00")
    if config.start_hour * 60 + (config.start_minute or 0) >= config.end_hour * 60 + (config.end_minute or 0):
        blockers.append("Sending window must end after it starts")
    if len(set(config.lead_ids)) != len(config.lead_ids):
        blockers.append("Select each lead once")
    if not config.lead_ids:
        blockers.append("Choose campaign recipients")
    leads = (
        await session.scalars(select(Lead).where(Lead.partner_id == partner.id, Lead.id.in_(config.lead_ids)))
    ).all()
    if len(leads) != len(set(config.lead_ids)):
        blockers.append("Some recipients are unavailable in this partner workspace")
    permitted = [x for x in leads if not exclusion_reasons(x)]
    if len(permitted) != len(leads):
        blockers.append("Every selected lead needs eligible status, recorded SMS permission and no opt-out")
    if any(x.permission_evidence.startswith("[TEST ONLY]") for x in leads):
        blockers.append("Test-only contacts cannot receive real SMS. Use real contacts with verified permission for outreach.")
    needs_recheck = campaign.status == "ready_to_connect" and (
        campaign.prepared_partner_revision != partner.revision
        or campaign.prepared_lead_revisions != {str(x.id): x.revision for x in leads}
    )
    if needs_recheck:
        blockers.append("Workspace or recipients changed since preparation; prepare again")
    if campaign.owner_hold:
        blockers.append("Campaign has an owner hold")
    if campaign.status in ("paused", "archived"):
        blockers.append("Campaign is " + campaign.status)
    return CampaignRead(
        owner_hold=campaign.owner_hold,
        id=campaign.id,
        partner_id=partner.id,
        status=campaign.status,
        revision=campaign.revision,
        config=config,
        updated_at=campaign.updated_at,
        blockers=blockers,
        recipient_count=len(permitted),
        needs_recheck=needs_recheck,
    )


@router.get("/messaging/capabilities")
async def capabilities(actor: User):
    return {"provider_connected": False, "outbound_enabled": False, "inbound_enabled": False}


@router.put("/partners/{partner_id}/leads/{lead_id}/sms-permission", response_model=LeadRead)
async def permission(
    partner_id: uuid.UUID, lead_id: uuid.UUID, body: PermissionUpdate, actor: User, session: Session
):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.partner_id == partner_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")
    revision(lead, body.expected_revision)
    if partner.status == "paused":
        raise HTTPException(409, "Partner is paused")
    if len(body.evidence.strip()) < 8:
        raise HTTPException(422, "Record permission evidence or revocation reason")
    if lead.opted_out and body.permission == "recorded":
        raise HTTPException(409, "Opted-out contacts cannot be re-enabled through this endpoint")
    lead.sms_permission = body.permission
    lead.permission_evidence = ("[TEST ONLY] " if lead.permission_evidence.startswith("[TEST ONLY]") else "") + body.evidence.strip()
    if body.permission == "revoked":
        lead.opted_out = True
    lead.status = "eligible" if body.permission == "recorded" and not lead.opted_out else "excluded"
    lead.revision += 1
    record_event(
        partner,
        actor,
        "sms_permission_updated",
        partner.status,
        f"Lead {lead.id}: {body.permission}; review status: {lead.status}; evidence: {body.evidence.strip()}",
        session,
    )
    await session.commit()
    return lead


@router.post("/partners/{partner_id}/campaigns", response_model=CampaignRead, status_code=201)
async def create(partner_id: uuid.UUID, body: CampaignConfig, actor: User, session: Session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    if partner.status == "paused":
        raise HTTPException(409, "Partner is paused")
    campaign = Campaign(
        partner_id=partner_id,
        config=body.model_dump(mode="json"),
        status="draft",
        revision=0,
        prepared_lead_revisions={},
        updated_at=datetime.now(timezone.utc),
    )
    session.add(campaign)
    await session.flush()
    event(campaign, actor, "created", "Campaign draft created", session)
    await session.commit()
    return await campaign_read(partner, campaign, session)


@router.get("/partners/{partner_id}/campaigns", response_model=list[CampaignRead])
async def campaigns(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    partner = await accessible_partner(partner_id, actor, session)
    rows = (
        await session.scalars(
            select(Campaign)
            .where(Campaign.partner_id == partner_id)
            .order_by(Campaign.updated_at.desc(), Campaign.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [await campaign_read(partner, row, session) for row in rows]


@router.get("/partners/{partner_id}/campaigns/{campaign_id}", response_model=CampaignRead)
async def detail(partner_id: uuid.UUID, campaign_id: uuid.UUID, actor: User, session: Session):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session)
    return await campaign_read(partner, campaign, session)


@router.put("/partners/{partner_id}/campaigns/{campaign_id}", response_model=CampaignRead)
async def save(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: CampaignUpdate, actor: User, session: Session
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    revision(campaign, body.expected_revision)
    if campaign.status in ("paused", "archived") or partner.status == "paused":
        raise HTTPException(409, "Resume before editing; archived campaigns cannot be edited")
    campaign.config = body.config.model_dump(mode="json")
    campaign.status = "draft"
    campaign.prepared_partner_revision = None
    campaign.prepared_lead_revisions = {}
    event(campaign, actor, "draft_saved", "Campaign settings changed; preparation reset", session)
    await session.commit()
    return await campaign_read(partner, campaign, session)


def get_opening_agent():
    # Imported on request to avoid the Test AI router's campaign dependency cycle.
    from .ai_tests import get_agent

    return get_agent()


@router.post("/partners/{partner_id}/campaigns/{campaign_id}/prepare", response_model=CampaignRead)
async def prepare(
    partner_id: uuid.UUID,
    campaign_id: uuid.UUID,
    body: RevisionRequest,
    actor: User,
    session: Session,
    agent=Depends(get_opening_agent),
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    revision(campaign, body.expected_revision)
    if campaign.status in ("paused", "archived"):
        raise HTTPException(409, "Campaign cannot be prepared in its current status")
    campaign.status = "draft"
    snapshot = await campaign_read(partner, campaign, session)
    if snapshot.blockers:
        raise HTTPException(422, detail={"blockers": snapshot.blockers})
    leads = (
        await session.scalars(
            select(Lead).where(Lead.partner_id == partner_id, Lead.id.in_(snapshot.config.lead_ids))
        )
    ).all()
    from .ai_tests import campaign_context

    context = campaign_context(partner, snapshot.config)
    context["opening_requirements"] = (
        "Write an opening SMS under 480 characters including the business name and Reply STOP to opt out."
    )
    opening = await agent.generate(context, [])
    message = opening.reply.strip()
    if (
        opening.decision != "continue"
        or not message
        or len(message) > 480
        or context["business_name"].casefold() not in message.casefold()
        or not re.search(r"\breply\s+stop\s+to\s+opt\s+out\b", message, re.IGNORECASE)
    ):
        raise HTTPException(
            502, "The AI could not create a valid opening message. Please check readiness again."
        )
    config = snapshot.config
    config.message_template = message
    campaign.config = config.model_dump(mode="json")
    campaign.status = "ready_to_connect"
    campaign.prepared_partner_revision = partner.revision
    campaign.prepared_lead_revisions = {str(x.id): x.revision for x in leads}
    event(campaign, actor, "prepared", "Self-service preparation complete; no messages sent", session)
    await session.commit()
    return await campaign_read(partner, campaign, session)


@router.post("/partners/{partner_id}/campaigns/{campaign_id}/control", response_model=CampaignRead)
async def control(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: CampaignControl, actor: User, session: Session
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    revision(campaign, body.expected_revision)
    if len(body.reason.strip()) < 5:
        raise HTTPException(422, "Explain the status change")
    if campaign.status == "archived":
        raise HTTPException(409, "Campaign is archived")
    owner = await is_owner(actor, session)
    if campaign.owner_hold and not owner:
        raise HTTPException(403, "Only the owner can release or change an owner-held campaign")
    if body.action == "resume":
        if campaign.status != "paused":
            raise HTTPException(409, "Campaign is not paused")
        if partner.status == "paused":
            raise HTTPException(409, "Partner is paused")
        campaign.status = "draft"
        campaign.owner_hold = False
    elif body.action == "pause":
        campaign.status = "paused"
        if owner:
            campaign.owner_hold = True
    else:
        campaign.status = "archived"
    campaign.prepared_partner_revision = None
    campaign.prepared_lead_revisions = {}
    event(campaign, actor, body.action, body.reason.strip(), session)
    await session.commit()
    return await campaign_read(partner, campaign, session)


@router.get("/partners/{partner_id}/campaigns/{campaign_id}/history", response_model=list[CampaignEventRead])
async def history(
    partner_id: uuid.UUID,
    campaign_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await get_campaign(partner_id, campaign_id, actor, session)
    return (
        await session.scalars(
            select(CampaignEvent)
            .where(CampaignEvent.campaign_id == campaign_id)
            .order_by(CampaignEvent.revision.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post("/partners/{partner_id}/campaigns/{campaign_id}/launch")
async def launch(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: RevisionRequest, actor: User, session: Session
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    revision(campaign, body.expected_revision)
    snapshot = await campaign_read(partner, campaign, session)
    if snapshot.blockers:
        raise HTTPException(422, detail={"blockers": snapshot.blockers})
    if campaign.status != "ready_to_connect":
        raise HTTPException(409, "Prepare this campaign before starting messages")
    if not snapshot.config.message_template.strip():
        raise HTTPException(422, "Check campaign readiness to generate the AI opening message first")
    if not re.search(r"\breply\s+stop\s+to\s+opt\s+out\b", snapshot.config.message_template, re.IGNORECASE):
        raise HTTPException(422, "Include Reply STOP to opt out in your opening SMS")
    # Intentional integration boundary: no job, fake receipt or delivery side effect.
    raise HTTPException(503, "SMS provider connection required. Nothing was queued or sent.")


@router.get("/owner/campaigns", response_model=list[CampaignRead])
async def owner_campaigns(
    actor: OwnerUser, session: Session, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
):
    rows = (
        await session.scalars(
            select(Campaign).order_by(Campaign.updated_at.desc(), Campaign.id).limit(limit).offset(offset)
        )
    ).all()
    result = []
    for row in rows:
        result.append(await campaign_read(await session.get(Partner, row.partner_id), row, session))
    return result


@router.post(
    "/partners/{partner_id}/campaigns/{campaign_id}/conversations",
    response_model=ConversationRead,
    status_code=201,
)
async def open_conversation(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: ConversationCreate, actor: User, session: Session
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    if partner.status == "paused" or campaign.status in ("paused", "archived"):
        raise HTTPException(409, "Tracking is paused or archived")
    if len(body.note.strip()) < 5:
        raise HTTPException(422, "Add an internal tracking note")
    if str(body.lead_id) not in campaign.config.get("lead_ids", []):
        raise HTTPException(422, "Select a recipient from this campaign")
    lead = await session.scalar(select(Lead).where(Lead.id == body.lead_id, Lead.partner_id == partner_id))
    if lead is None:
        raise HTTPException(404, "Lead not found")
    existing = await session.scalar(
        select(Conversation).where(
            Conversation.campaign_id == campaign_id, Conversation.lead_id == body.lead_id
        )
    )
    if existing:
        raise HTTPException(409, "This recipient already has a tracking record")
    conversation = Conversation(
        campaign_id=campaign_id,
        lead_id=body.lead_id,
        status="new",
        revision=0,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(conversation)
    await session.flush()
    session.add(
        ConversationEvent(
            conversation_id=conversation.id,
            actor_id=actor.user_id,
            status="new",
            note=body.note.strip(),
            revision=0,
        )
    )
    event(campaign, actor, "tracking_opened", f"Manual tracking for lead {body.lead_id}", session)
    await session.commit()
    return conversation


@router.get(
    "/partners/{partner_id}/campaigns/{campaign_id}/conversations", response_model=list[ConversationRead]
)
async def conversations(
    partner_id: uuid.UUID,
    campaign_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await get_campaign(partner_id, campaign_id, actor, session)
    return (
        await session.scalars(
            select(Conversation)
            .where(Conversation.campaign_id == campaign_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.patch(
    "/partners/{partner_id}/campaigns/{campaign_id}/conversations/{conversation_id}",
    response_model=ConversationRead,
)
async def update_conversation(
    partner_id: uuid.UUID,
    campaign_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    actor: User,
    session: Session,
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    if partner.status == "paused" or campaign.status in ("paused", "archived"):
        raise HTTPException(409, "Tracking is paused or archived")
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.campaign_id == campaign_id)
        .with_for_update()
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    revision(conversation, body.expected_revision)
    if len(body.note.strip()) < 5:
        raise HTTPException(422, "Add an internal tracking note")
    conversation.status = body.status
    conversation.revision += 1
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(
        ConversationEvent(
            conversation_id=conversation.id,
            actor_id=actor.user_id,
            status=body.status,
            note=body.note.strip(),
            revision=conversation.revision,
        )
    )
    event(campaign, actor, "tracking_updated", f"Manual record {conversation.id}: {body.status}", session)
    await session.commit()
    return conversation


@router.get(
    "/partners/{partner_id}/campaigns/{campaign_id}/conversations/{conversation_id}/history",
    response_model=list[ConversationEventRead],
)
async def conversation_history(
    partner_id: uuid.UUID,
    campaign_id: uuid.UUID,
    conversation_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await get_campaign(partner_id, campaign_id, actor, session)
    value = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.campaign_id == campaign_id
        )
    )
    if value is None:
        raise HTTPException(404, "Conversation not found")
    return (
        await session.scalars(
            select(ConversationEvent)
            .where(ConversationEvent.conversation_id == conversation_id)
            .order_by(ConversationEvent.revision.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.post("/partners/{partner_id}/campaign-import", response_model=CampaignRead)
async def campaign_import(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    file: UploadFile = File(),
    config: str = Form(max_length=100000),
    expected_partner_revision: int = Form(ge=0),
    phone_column: str = Form(max_length=200),
    name_column: str | None = Form(default=None, max_length=200),
    email_column: str | None = Form(default=None, max_length=200),
    permission_columns: str = Form(default="{}", max_length=2000),
    permission_attested: bool = Form(default=False),
    sheet: str | None = Form(default=None, max_length=200),
    campaign_id: uuid.UUID | None = Form(default=None),
    expected_revision: int | None = Form(default=None, ge=0),
):
    await accessible_partner(partner_id, actor, session)
    try:
        body = CampaignConfig.model_validate_json(config)
    except ValueError:
        raise HTTPException(422, "Check campaign details before saving the import") from None
    try:
        parsed = await run_in_threadpool(
            parse_file,
            await file.read(MAX_BYTES + 1),
            file.filename,
            sheet,
            phone_column,
            name_column,
            email_column,
            permission_columns,
            permission_attested,
        )
    finally:
        await file.close()
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    revision(partner, expected_partner_revision)
    if partner.status == "paused":
        raise HTTPException(409, "Business is paused")
    if campaign_id:
        _, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
        if expected_revision is None:
            raise HTTPException(422, "Campaign revision is required")
        revision(campaign, expected_revision)
        if campaign.status in ("paused", "archived"):
            raise HTTPException(409, "Resume before editing; archived campaigns cannot be edited")
    else:
        campaign = Campaign(
            partner_id=partner_id,
            status="draft",
            revision=0,
            config={},
            prepared_lead_revisions={},
            updated_at=datetime.now(timezone.utc),
        )
        session.add(campaign)
        await session.flush()
    existing = {
        lead.phone: lead
        for lead in (await session.scalars(select(Lead).where(Lead.partner_id == partner_id))).all()
    }
    accepted, duplicates, _ = preview_rows(parsed, existing)
    batch = LeadImport(
        partner_id=partner_id,
        actor_id=actor.user_id,
        file_name=PurePath((file.filename or "leads.csv").replace("\\", "/")).name[:200],
        total_rows=parsed.total,
        imported=len(accepted),
        duplicates=duplicates,
        invalid=parsed.invalid,
    )
    session.add(batch)
    await session.flush()
    for contact in accepted:
        lead = Lead(partner_id=partner_id, import_id=batch.id, **contact)
        session.add(lead)
        existing[lead.phone] = lead
    await session.flush()
    # Apply permission data to duplicates conservatively; never re-enable an opt-out.
    for contact in parsed.contacts:
        lead = existing[contact["phone"]]
        if "sms_permission" not in contact:
            continue
        if contact["opted_out"]:
            lead.opted_out = True
            lead.sms_permission = "revoked"
            lead.status = "excluded"
            lead.permission_evidence = contact["permission_evidence"] or "Imported opt-out"
            lead.revision += 1
        elif not lead.opted_out and lead.sms_permission != "revoked" and contact["sms_permission"] == "recorded":
            # Test records cannot overwrite a real contact's permission.
            if contact["permission_evidence"].startswith("[TEST ONLY]") and lead.permission_evidence and not lead.permission_evidence.startswith("[TEST ONLY]"):
                continue
            lead.sms_permission = "recorded"
            lead.status = "eligible"
            lead.permission_evidence = ("[TEST ONLY] " if lead.permission_evidence.startswith("[TEST ONLY]") and not contact["permission_evidence"].startswith("[TEST ONLY]") else "") + contact["permission_evidence"]
            lead.revision += 1

    attached = list(dict.fromkeys(body.lead_ids + [existing[c["phone"]].id for c in parsed.contacts]))
    if not parsed.contacts:
        raise HTTPException(422, "No valid leads to add. Fix the file and preview it again.")
    if len(attached) > 10000:
        raise HTTPException(422, "A campaign supports up to 10,000 distinct leads. Upload a smaller list.")
    scoped = set(
        (
            await session.scalars(select(Lead.id).where(Lead.partner_id == partner_id, Lead.id.in_(attached)))
        ).all()
    )
    if scoped != set(attached):
        raise HTTPException(422, "Recipients must belong to this business")
    body.lead_ids = attached
    campaign.config = body.model_dump(mode="json")
    campaign.status = "draft"
    campaign.prepared_partner_revision = None
    campaign.prepared_lead_revisions = {}
    partner.readiness = {}
    record_event(
        partner,
        actor,
        "leads_imported",
        partner.status,
        f"{len(accepted)} imported; {duplicates} duplicates; {parsed.invalid} invalid",
        session,
    )
    event(
        campaign,
        actor,
        "draft_saved",
        f"Contact file imported; {len(attached)} recipients; preparation reset",
        session,
    )
    await session.commit()
    return await campaign_read(partner, campaign, session)


@router.post("/partners/{partner_id}/leads/{lead_id}/contact-review", response_model=LeadRead)
async def contact_review(
    partner_id: uuid.UUID, lead_id: uuid.UUID, body: ContactReview, actor: User, session: Session
):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    if partner.status == "paused":
        raise HTTPException(409, "Business is paused")
    lead = await session.scalar(
        select(Lead).where(Lead.partner_id == partner_id, Lead.id == lead_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")
    revision(lead, body.expected_revision)
    if len(body.evidence.strip()) < 8:
        raise HTTPException(422, "Describe the permission evidence or exclusion reason")
    if body.action == "record_permission":
        if lead.opted_out or lead.sms_permission == "revoked":
            raise HTTPException(409, "Opted-out contacts cannot be re-enabled here")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.consent_date):
                raise ValueError
            consent_date = date.fromisoformat(body.consent_date)
        except ValueError:
            raise HTTPException(422, "Provide the consent date as YYYY-MM-DD") from None
        if consent_date > datetime.now(timezone.utc).date():
            raise HTTPException(422, "Consent date cannot be in the future")
        if (
            not body.attested
            or not body.source.strip()
            or not (len(body.country) == 2 and body.country.isascii() and body.country.isalpha())
        ):
            raise HTTPException(422, "Confirm business-specific marketing SMS permission, source and country")
        lead.sms_permission = "recorded"
        lead.status = "eligible"
        lead.permission_evidence = (
            ("[TEST ONLY] " if lead.permission_evidence.startswith("[TEST ONLY]") else "") + f"Business: {partner.name}; Source: {body.source.strip()}; "
            f"Consent date: {body.consent_date}; Country: {body.country.upper()}; "
            f"Business-specific marketing SMS reviewed: yes. {body.evidence.strip()}"
        )
    elif body.action == "opt_out":
        lead.opted_out = True
        lead.sms_permission = "revoked"
        lead.status = "excluded"
        lead.permission_evidence = ("[TEST ONLY] " if lead.permission_evidence.startswith("[TEST ONLY]") else "") + body.evidence.strip()
    else:
        lead.status = "excluded"
    lead.revision += 1
    record_event(
        partner,
        actor,
        "contact_reviewed",
        partner.status,
        f"Lead {lead.id}: {body.action}; evidence: {body.evidence.strip()}",
        session,
    )
    await session.commit()
    return lead


@router.put(
    "/partners/{partner_id}/campaigns/{campaign_id}/recipients", response_model=RecipientSelectionRead
)
async def select_recipients(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: RecipientSelection, actor: User, session: Session
):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, True)
    revision(campaign, body.expected_revision)
    if campaign.status in ("paused", "archived") or partner.status == "paused" or campaign.owner_hold:
        raise HTTPException(
            409, "Recipients cannot be changed while campaign or business is held, paused or archived"
        )
    requested = list(dict.fromkeys(body.lead_ids))
    leads = (
        await session.scalars(
            select(Lead).where(Lead.partner_id == partner_id, Lead.id.in_(requested)).with_for_update()
        )
    ).all()
    if len(leads) != len(requested):
        raise HTTPException(422, "Recipients must belong to this business")
    permitted = {x.id for x in leads if not exclusion_reasons(x)}
    selected = [x for x in requested if x in permitted]
    config = CampaignConfig.model_validate(campaign.config)
    config.lead_ids = selected
    campaign.config = config.model_dump(mode="json")
    campaign.status = "draft"
    campaign.prepared_partner_revision = None
    campaign.prepared_lead_revisions = {}
    event(
        campaign,
        actor,
        "recipients_selected",
        f"{len(selected)} selected; {len(requested) - len(selected)} excluded by permission checks",
        session,
    )
    await session.commit()
    return RecipientSelectionRead(
        campaign=await campaign_read(partner, campaign, session), removed_count=len(requested) - len(selected)
    )


@router.get("/partners/{partner_id}/eligible-recipient-ids", response_model=list[uuid.UUID])
async def eligible_recipient_ids(partner_id: uuid.UUID, actor: User, session: Session,
                                 campaign_id: uuid.UUID | None = None, import_id: uuid.UUID | None = None,
                                 search: str = Query("", max_length=200)):
    from sqlalchemy import or_

    from .contact_policy import eligible_expression

    await accessible_partner(partner_id, actor, session)
    query = select(Lead.id).where(Lead.partner_id == partner_id, eligible_expression(Lead))
    if campaign_id:
        _, campaign = await get_campaign(partner_id, campaign_id, actor, session)
        query = query.where(Lead.id.in_([uuid.UUID(value) for value in campaign.config.get("lead_ids", [])]))
    if import_id:
        query = query.where(Lead.import_id == import_id)
    if search.strip():
        needle = search.strip().lower()
        query = query.where(or_(func.lower(Lead.name).contains(needle, autoescape=True),
                                func.lower(Lead.email).contains(needle, autoescape=True),
                                Lead.phone.contains(needle, autoescape=True)))
    ids = (await session.scalars(query.order_by(Lead.id).limit(10001))).all()
    if len(ids) > 10000:
        raise HTTPException(422, "More than 10,000 eligible leads match. Narrow the list or search first.")
    return ids
