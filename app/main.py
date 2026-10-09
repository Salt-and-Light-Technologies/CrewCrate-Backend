import uuid
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, accessible_partner, current_actor, is_owner, require_owner
from .campaigns import router as campaign_router
from .config import get_settings
from .database import get_session
from .imports import MAX_BYTES, parse_csv, preview_rows
from .limits import BodyLimitMiddleware
from .models import Event, Lead, LeadImport, Member, Partner
from .schemas import (
    SalesHandoffEmailAdd,
    Decision,
    DraftUpdate,
    EventRead,
    ImportPreview,
    ImportRead,
    LeadRead,
    LeadStatus,
    LeadUpdate,
    LeadWorkspaceRead,
    PartnerCreate,
    PartnerRead,
    PartnerStatus,
    ReadinessUpdate,
    RevisionRequest,
)
from .workflow import email_valid, apply_decision, check_revision, launch_issues, onboarding_issues, record_event

app = FastAPI(title="CrewCrate Revenue Recovery API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(BodyLimitMiddleware)
Session = Annotated[AsyncSession, Depends(get_session)]
User = Annotated[Actor, Depends(current_actor)]
OwnerUser = Annotated[Actor, Depends(require_owner)]


async def read_partner(partner, session):
    count = await session.scalar(select(func.count()).select_from(Lead).where(Lead.partner_id == partner.id))
    return PartnerRead(
        id=partner.id,
        name=partner.name,
        status=partner.status,
        revision=partner.revision,
        onboarding=partner.onboarding,
        readiness=partner.readiness,
        paused_from=partner.paused_from,
        updated_at=partner.updated_at,
        missing_launch_requirements=launch_issues(partner, count),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "crewcrate-api", "version": "0.3.0"}


@app.get("/v1/me")
async def me(actor: User, session: Session):
    return {"user_id": actor.user_id, "is_owner": await is_owner(actor, session)}


@app.post("/v1/partners", response_model=PartnerRead, status_code=201)
async def create_partner(body: PartnerCreate, actor: OwnerUser, session: Session):
    partner = Partner(
        name=body.name.strip(),
        onboarding={},
        readiness={},
        revision=0,
        status="draft",
        updated_at=datetime.now(timezone.utc),
    )
    session.add(partner)
    await session.flush()
    session.add(Member(partner_id=partner.id, user_id=body.partner_user_id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(422, "Partner user must be an existing Supabase account") from None
    return await read_partner(partner, session)


@app.get("/v1/partners", response_model=list[PartnerRead])
async def partners(
    actor: User,
    session: Session,
    status: PartnerStatus | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    query = select(Partner)
    if not await is_owner(actor, session):
        query = query.join(Member, Member.partner_id == Partner.id).where(Member.user_id == actor.user_id)
    if status:
        query = query.where(Partner.status == status.value)
    rows = (
        await session.scalars(
            query.order_by(Partner.updated_at.desc(), Partner.id).limit(limit).offset(offset)
        )
    ).all()
    return [await read_partner(row, session) for row in rows]


@app.get("/v1/partners/{partner_id}", response_model=PartnerRead)
async def partner_detail(partner_id: uuid.UUID, actor: User, session: Session):
    return await read_partner(await accessible_partner(partner_id, actor, session), session)


@app.put("/v1/partners/{partner_id}/onboarding", response_model=PartnerRead)
async def save_draft(partner_id: uuid.UUID, body: DraftUpdate, actor: User, session: Session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    check_revision(partner, body.expected_revision)
    if partner.status not in ("draft", "changes_requested"):
        raise HTTPException(409, "Onboarding must be draft or changes requested to edit")
    previous = partner.status
    updated_setup = body.onboarding.model_dump()
    updated_setup["handoff_emails"] = partner.onboarding.get("handoff_emails", [])
    partner.onboarding = updated_setup
    if body.onboarding.business_name.strip():
        partner.name = body.onboarding.business_name.strip()[:160]
    # All attestations must be reviewed again after an edit.
    partner.readiness = {}
    record_event(partner, actor, "draft_saved", previous, "", session)
    await session.commit()
    return await read_partner(partner, session)


@app.post("/v1/partners/{partner_id}/submit", response_model=PartnerRead)
async def submit(partner_id: uuid.UUID, body: RevisionRequest, actor: User, session: Session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    check_revision(partner, body.expected_revision)
    if partner.status not in ("draft", "changes_requested"):
        raise HTTPException(409, "Partner cannot submit from current status")
    issues = onboarding_issues(partner.onboarding)
    if issues:
        raise HTTPException(422, detail={"onboarding_issues": issues})
    previous = partner.status
    partner.status = "submitted"
    record_event(partner, actor, "submitted", previous, "", session)
    await session.commit()
    return await read_partner(partner, session)


@app.put("/v1/partners/{partner_id}/readiness", response_model=PartnerRead)
async def readiness(partner_id: uuid.UUID, body: ReadinessUpdate, actor: OwnerUser, session: Session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    check_revision(partner, body.expected_revision)
    if len(body.reason.strip()) < 5:
        raise HTTPException(422, "Explain the readiness review")
    if partner.status in ("pilot_approved", "paused"):
        raise HTTPException(409, "Approved or paused readiness cannot be edited")
    partner.readiness = body.readiness.model_dump()
    record_event(partner, actor, "readiness_reviewed", partner.status, body.reason.strip(), session)
    await session.commit()
    return await read_partner(partner, session)


@app.post("/v1/partners/{partner_id}/decisions", response_model=PartnerRead)
async def decision(partner_id: uuid.UUID, body: Decision, actor: OwnerUser, session: Session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    check_revision(partner, body.expected_revision)
    snapshot = await read_partner(partner, session)
    previous, reason = apply_decision(
        partner, body.action.value, body.reason, snapshot.missing_launch_requirements
    )
    record_event(partner, actor, body.action.value, previous, reason, session)
    await session.commit()
    return await read_partner(partner, session)


@app.get("/v1/partners/{partner_id}/history", response_model=list[EventRead])
async def history(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await accessible_partner(partner_id, actor, session)
    return (
        await session.scalars(
            select(Event)
            .where(Event.partner_id == partner_id)
            .order_by(Event.revision.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()


@app.post("/v1/partners/{partner_id}/imports", response_model=ImportRead, status_code=201)
async def import_leads(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    file: UploadFile = File(),
    phone_column: str = Form(max_length=200),
    expected_revision: int = Form(ge=0),
    name_column: str | None = Form(default=None, max_length=200),
    email_column: str | None = Form(default=None, max_length=200),
):
    await accessible_partner(partner_id, actor, session)
    try:
        raw = await file.read(MAX_BYTES + 1)
        parsed = parse_csv(raw, phone_column, name_column, email_column)
    finally:
        await file.close()
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    check_revision(partner, expected_revision)
    if partner.status == "paused":
        raise HTTPException(409, "CSV uploads are unavailable while the partner is paused")
    existing = set((await session.scalars(select(Lead.phone).where(Lead.partner_id == partner_id))).all())
    accepted, duplicates, _ = preview_rows(parsed, existing)
    # Persist summaries and normalized contacts only; original files are discarded.
    name = PurePath((file.filename or "leads.csv").replace("\\", "/")).name[:200]
    batch = LeadImport(
        partner_id=partner_id,
        actor_id=actor.user_id,
        file_name=name,
        total_rows=parsed.total,
        imported=len(accepted),
        duplicates=duplicates,
        invalid=parsed.invalid,
    )
    session.add(batch)
    await session.flush()
    for contact in accepted:
        session.add(Lead(partner_id=partner_id, import_id=batch.id, **contact))
    partner.readiness = {}
    record_event(
        partner,
        actor,
        "leads_imported",
        partner.status,
        f"{len(accepted)} imported; {duplicates} duplicates; {parsed.invalid} invalid",
        session,
    )
    await session.commit()
    return batch


@app.get("/v1/partners/{partner_id}/imports", response_model=list[ImportRead])
async def imports(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await accessible_partner(partner_id, actor, session)
    return (
        await session.scalars(
            select(LeadImport)
            .where(LeadImport.partner_id == partner_id)
            .order_by(LeadImport.timestamp.desc(), LeadImport.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@app.get("/v1/partners/{partner_id}/leads", response_model=list[LeadRead])
async def leads(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: LeadStatus | None = None,
    search: str = Query("", max_length=200),
    import_id: uuid.UUID | None = None,
):
    await accessible_partner(partner_id, actor, session)
    query = select(Lead).where(Lead.partner_id == partner_id)
    if import_id:
        query = query.where(Lead.import_id == import_id)
    if status:
        query = query.where(Lead.status == status.value)
    if search.strip():
        needle = search.strip().lower()
        query = query.where(
            or_(
                func.lower(Lead.name).contains(needle, autoescape=True),
                func.lower(Lead.email).contains(needle, autoescape=True),
                Lead.phone.contains(needle, autoescape=True),
            )
        )
    return (await session.scalars(query.order_by(Lead.id).limit(limit).offset(offset))).all()


@app.patch("/v1/partners/{partner_id}/leads/{lead_id}", response_model=LeadRead)
async def lead_status(
    partner_id: uuid.UUID, lead_id: uuid.UUID, body: LeadUpdate, actor: User, session: Session
):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.partner_id == partner_id).with_for_update()
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")
    if lead.revision != body.expected_revision:
        raise HTTPException(409, "Lead changed; refresh before saving")
    if partner.status == "paused":
        raise HTTPException(409, "Partner is paused")
    previous = lead.status
    lead.status = body.status.value
    lead.revision += 1
    record_event(
        partner,
        actor,
        "lead_status_changed",
        partner.status,
        f"Lead {lead.id}: {previous} -> {lead.status}",
        session,
    )
    await session.commit()
    return lead


@app.get("/v1/owner/overview")
async def overview(actor: OwnerUser, session: Session):
    statuses = dict(
        (await session.execute(select(Partner.status, func.count()).group_by(Partner.status))).all()
    )
    lead_statuses = dict(
        (await session.execute(select(Lead.status, func.count()).group_by(Lead.status))).all()
    )
    return {
        "partner_status_counts": statuses,
        "lead_status_counts": lead_statuses,
        "import_count": await session.scalar(select(func.count()).select_from(LeadImport)),
        "measurement_scope": "Stored onboarding, imports and lead review only; no revenue or campaign metrics yet",
    }


async def workspace_summary(partner, session):
    counts = dict(
        (
            await session.execute(
                select(Lead.status, func.count()).where(Lead.partner_id == partner.id).group_by(Lead.status)
            )
        ).all()
    )
    imports = await session.scalar(
        select(func.count()).select_from(LeadImport).where(LeadImport.partner_id == partner.id)
    )
    return LeadWorkspaceRead(
        partner_id=partner.id,
        name=partner.name,
        status=partner.status,
        partner_revision=partner.revision,
        total_leads=sum(counts.values()),
        import_count=imports,
        lead_counts=counts,
    )


@app.get("/v1/partners/{partner_id}/lead-workspace", response_model=LeadWorkspaceRead)
async def lead_workspace(partner_id: uuid.UUID, actor: User, session: Session):
    return await workspace_summary(await accessible_partner(partner_id, actor, session), session)


@app.get("/v1/owner/lead-workspaces", response_model=list[LeadWorkspaceRead])
async def owner_lead_workspaces(
    actor: OwnerUser, session: Session, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
):
    rows = (
        await session.scalars(
            select(Partner).order_by(Partner.updated_at.desc(), Partner.id).limit(limit).offset(offset)
        )
    ).all()
    return [await workspace_summary(row, session) for row in rows]


@app.post("/v1/partners/{partner_id}/imports/preview", response_model=ImportPreview)
async def preview_import(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    file: UploadFile = File(),
    phone_column: str = Form(max_length=200),
    name_column: str | None = Form(default=None, max_length=200),
    email_column: str | None = Form(default=None, max_length=200),
):
    partner = await accessible_partner(partner_id, actor, session)
    if partner.status == "paused":
        raise HTTPException(409, "CSV uploads are unavailable while the partner is paused")
    try:
        parsed = parse_csv(await file.read(MAX_BYTES + 1), phone_column, name_column, email_column)
    finally:
        await file.close()
    existing = (await session.scalars(select(Lead.phone).where(Lead.partner_id == partner_id))).all()
    accepted, duplicates, issues = preview_rows(parsed, existing)
    return ImportPreview(
        partner_revision=partner.revision,
        total_rows=parsed.total,
        imported=len(accepted),
        duplicates=duplicates,
        invalid=parsed.invalid,
        issues=issues[:100],
        issues_truncated=len(issues) > 100,
    )


@app.get("/v1/partners/{partner_id}/lead-activity", response_model=list[EventRead])
async def lead_activity(
    partner_id: uuid.UUID,
    actor: User,
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await accessible_partner(partner_id, actor, session)
    return (
        await session.scalars(
            select(Event)
            .where(
                Event.partner_id == partner_id,
                Event.action.in_(["leads_imported", "lead_status_changed", "sms_permission_updated"]),
            )
            .order_by(Event.revision.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()


app.include_router(campaign_router)


def stored_handoff_emails(partner):
    values = partner.onboarding.get("handoff_emails", [])
    legacy = partner.onboarding.get("handoff_email", "").strip().lower()
    if legacy and email_valid(legacy):
        values = [legacy] + values
    return list(dict.fromkeys(value.strip().lower() for value in values if email_valid(value.strip())))


@app.get("/v1/partners/{partner_id}/handoff-emails")
async def handoff_emails(partner_id: uuid.UUID, actor: User, session: Session):
    partner = await accessible_partner(partner_id, actor, session)
    return {"emails": stored_handoff_emails(partner)}


@app.post("/v1/partners/{partner_id}/handoff-emails")
async def add_handoff_email(partner_id: uuid.UUID, body: SalesHandoffEmailAdd, actor: User, session: Session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    email = body.email.strip().lower()
    if not email_valid(email):
        raise HTTPException(422, "Provide a valid sales handoff email")
    emails = stored_handoff_emails(partner)
    if email not in emails:
        emails.append(email)
        partner.onboarding = {**partner.onboarding, "handoff_emails": emails}
        record_event(partner, actor, "sales_handoff_email_added", partner.status, "Saved sales handoff destination", session)
        await session.commit()
    return {"emails": emails}
