import uuid
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, accessible_partner, current_actor, is_owner, require_owner
from .database import get_session
from .models import Campaign, CampaignEvent, Conversation, ConversationEvent, Lead, Partner
from .schemas import (
    CampaignConfig,
    CampaignControl,
    CampaignEventRead,
    CampaignRead,
    CampaignUpdate,
    ConversationCreate,
    ConversationEventRead,
    ConversationRead,
    ConversationUpdate,
    LeadRead,
    PermissionUpdate,
    RevisionRequest,
)
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
    permitted = [
        x
        for x in leads
        if x.status == "eligible"
        and x.sms_permission == "recorded"
        and bool(x.permission_evidence.strip())
        and not x.opted_out
    ]
    if len(permitted) != len(leads):
        blockers.append("Every selected lead needs eligible status, recorded SMS permission and no opt-out")
    needs_recheck = campaign.status == "ready_to_connect" and (
        campaign.prepared_partner_revision != partner.revision
        or campaign.prepared_lead_revisions != {str(x.id): x.revision for x in leads}
    )
    if needs_recheck:
        blockers.append("Workspace or recipients changed since preparation; prepare again")
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
    lead.permission_evidence = body.evidence.strip()
    if body.permission == "revoked":
        lead.opted_out = True
    lead.revision += 1
    record_event(
        partner,
        actor,
        "sms_permission_updated",
        partner.status,
        f"Lead {lead.id}: {body.permission}; evidence: {body.evidence.strip()}",
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


@router.post("/partners/{partner_id}/campaigns/{campaign_id}/prepare", response_model=CampaignRead)
async def prepare(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: RevisionRequest, actor: User, session: Session
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


@router.post("/partners/{partner_id}/campaigns/{campaign_id}/launch", status_code=503)
async def launch(
    partner_id: uuid.UUID, campaign_id: uuid.UUID, body: RevisionRequest, actor: User, session: Session
):
    _, campaign = await get_campaign(partner_id, campaign_id, actor, session)
    revision(campaign, body.expected_revision)
    raise HTTPException(503, "Messaging provider is not connected. Nothing was queued or sent")


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
