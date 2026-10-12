"""Public business applications do not grant workspace membership until owner approval."""
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, current_actor, require_owner
from .database import get_session
from .models import BusinessApplication, Member, Partner
from .workflow import email_valid, record_event

router = APIRouter(prefix="/v1")
Session = Annotated[AsyncSession, Depends(get_session)]
User = Annotated[Actor, Depends(current_actor)]
OwnerUser = Annotated[Actor, Depends(require_owner)]


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    business_name: str = Field(min_length=1, max_length=160)
    contact_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    phone: str = Field(min_length=7, max_length=40)
    referral: str = Field(default="", max_length=2000)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not email_valid(value):
            raise ValueError("Enter a valid contact email")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        digits = "".join(character for character in value if character.isascii() and character.isdigit())
        if not 7 <= len(digits) <= 15 or any(character not in "+0123456789 ()-." for character in value):
            raise ValueError("Enter a plausible contact phone number")
        return value


class ApplicationRead(ApplicationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    revision: int
    partner_id: uuid.UUID | None
    review_note: str
    submitted_at: datetime


class ApplicationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=0)
    action: Literal["approve", "deny"]
    note: str = Field(default="", max_length=2000)


@router.get("/me/application", response_model=ApplicationRead | None)
async def my_application(actor: User, session: Session):
    return await session.scalar(select(BusinessApplication).where(BusinessApplication.user_id == actor.user_id))


@router.post("/me/application", response_model=ApplicationRead)
async def apply(body: ApplicationCreate, actor: User, session: Session):
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": actor.user_id.int % (2**63 - 1)})
    existing = await session.scalar(select(BusinessApplication).where(
        BusinessApplication.user_id == actor.user_id))
    if existing:
        return existing
    if await session.scalar(select(Member).join(Partner).where(
        Member.user_id == actor.user_id, Partner.access_approved.is_(True)).limit(1)):
        raise HTTPException(409, "You already belong to a business")
    unapproved = await session.scalar(select(Member).join(Partner).where(
        Member.user_id == actor.user_id, Partner.access_approved.is_(False)).order_by(Member.partner_id).limit(1))
    application = BusinessApplication(user_id=actor.user_id, **body.model_dump(),
                                      status="pending", revision=0,
                                      partner_id=unapproved.partner_id if unapproved else None,
                                      submitted_at=datetime.now(timezone.utc))
    session.add(application)
    await session.commit()
    return application


@router.get("/owner/applications", response_model=list[ApplicationRead])
async def applications(actor: OwnerUser, session: Session,
                       status: Literal["pending", "approved", "denied"] = "pending",
                       limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return (await session.scalars(select(BusinessApplication).where(BusinessApplication.status == status)
            .order_by(BusinessApplication.submitted_at, BusinessApplication.id)
            .limit(limit).offset(offset))).all()


@router.post("/owner/applications/{application_id}/decision", response_model=ApplicationRead)
async def decide(application_id: uuid.UUID, body: ApplicationDecision,
                 actor: OwnerUser, session: Session):
    application = await session.scalar(select(BusinessApplication).where(
        BusinessApplication.id == application_id).with_for_update().execution_options(populate_existing=True))
    if application is None:
        raise HTTPException(404, "Application not found")
    if application.revision != body.expected_revision or application.status != "pending":
        raise HTTPException(409, "Request changed; refresh before reviewing")
    if body.action == "deny" and not body.note:
        raise HTTPException(422, "Provide a reason so the applicant knows what to do next")
    if body.action == "approve":
        partner = None
        if application.partner_id:
            membership = await session.get(Member, (application.partner_id, application.user_id))
            if membership is None:
                raise HTTPException(409, "Business membership changed; review the request again")
            partner = await session.scalar(select(Partner).where(Partner.id == application.partner_id)
                                           .with_for_update().execution_options(populate_existing=True))
        if partner is None:
            partner = Partner(name=application.business_name, onboarding={
                "business_name": application.business_name, "contact_name": application.contact_name,
                "email": application.email,
            }, readiness={}, revision=0, status="draft", updated_at=datetime.now(timezone.utc))
            session.add(partner)
            await session.flush()
            session.add(Member(partner_id=partner.id, user_id=application.user_id))
        partner.access_approved = True
        record_event(partner, actor, "business_application_approved", partner.status, body.note, session)
        application.partner_id = partner.id
    application.status = "approved" if body.action == "approve" else "denied"
    application.revision += 1
    application.reviewed_by = actor.user_id
    application.reviewed_at = datetime.now(timezone.utc)
    application.review_note = body.note
    await session.commit()
    return application
