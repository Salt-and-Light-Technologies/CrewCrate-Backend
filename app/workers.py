"""Business teams and durable worker inbox. No invitation email is sent."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, accessible_partner, current_actor, is_owner
from .database import get_session
from .models import (
    Campaign,
    Conversation,
    ConversationEvent,
    Lead,
    Member,
    Partner,
    WorkerDevice,
    WorkerInvitation,
    WorkerNotification,
    WorkerPushJob,
)
from .workflow import email_valid

router = APIRouter(prefix="/v1")
Session = Annotated[AsyncSession, Depends(get_session)]
User = Annotated[Actor, Depends(current_actor)]


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Invite(Body):
    email: str = Field(min_length=3, max_length=320)


class Accept(Body):
    token: str = Field(min_length=32, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)


async def admin(partner_id, actor, session):
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    # Platform oversight is separate from a business administrator's team management.
    if await is_owner(actor, session):
        raise HTTPException(403, "Invite workers from a business administrator account")
    return partner


def invitation_read(invitation):
    return dict(id=invitation.id, email=invitation.email, expires_at=invitation.expires_at,
                status="accepted" if invitation.consumed else "revoked" if invitation.revoked
                else "expired" if aware(invitation.expires_at) <= now() else "pending")


@router.get("/partners/{partner_id}/team")
async def team(partner_id: uuid.UUID, actor: User, session: Session):
    await admin(partner_id, actor, session)
    members = (await session.scalars(select(Member).where(Member.partner_id == partner_id))).all()
    invitations = (await session.scalars(select(WorkerInvitation).where(
        WorkerInvitation.partner_id == partner_id).order_by(WorkerInvitation.expires_at.desc()).limit(100))).all()
    return dict(members=[dict(user_id=m.user_id, display_name=m.display_name, role=m.role, active=m.active)
                         for m in members], invitations=[invitation_read(i) for i in invitations])


@router.post("/partners/{partner_id}/worker-invitations", status_code=201)
async def invite(partner_id: uuid.UUID, body: Invite, actor: User, session: Session):
    await admin(partner_id, actor, session)
    email = body.email.strip().casefold()
    if not email_valid(email):
        raise HTTPException(422, "Enter a valid worker email")
    # Rotation invalidates previous unaccepted codes for this recipient/business.
    existing = (await session.scalars(select(WorkerInvitation).where(
        WorkerInvitation.partner_id == partner_id, WorkerInvitation.email == email,
        WorkerInvitation.consumed.is_(False)))).all()
    for invitation in existing:
        invitation.revoked = True
    token = secrets.token_urlsafe(32)
    invitation = WorkerInvitation(partner_id=partner_id, email=email,
        token_hash=hashlib.sha256(token.encode()).hexdigest(), created_by=actor.user_id,
        expires_at=now() + timedelta(days=7))
    session.add(invitation)
    await session.commit()
    return {**invitation_read(invitation), "token": token, "delivery": "share_code"}


@router.delete("/partners/{partner_id}/worker-invitations/{invitation_id}")
async def revoke_invite(partner_id: uuid.UUID, invitation_id: uuid.UUID, actor: User, session: Session):
    await admin(partner_id, actor, session)
    invitation = await session.get(WorkerInvitation, invitation_id)
    if invitation is None or invitation.partner_id != partner_id:
        raise HTTPException(404, "Invitation not found")
    if invitation.consumed:
        raise HTTPException(409, "Invitation already accepted; remove the worker instead")
    invitation.revoked = True
    await session.commit()
    return {"ok": True}


@router.delete("/partners/{partner_id}/workers/{user_id}")
async def remove_worker(partner_id: uuid.UUID, user_id: uuid.UUID, actor: User, session: Session):
    await admin(partner_id, actor, session)
    member = await session.get(Member, (partner_id, user_id))
    if member is None or member.role != "worker":
        raise HTTPException(404, "Worker not found")
    member.active = False
    await session.commit()
    return {"ok": True}


@router.post("/me/worker-invitations/accept")
async def accept(body: Accept, actor: User, session: Session):
    if not actor.email_verified or not actor.email:
        raise HTTPException(403, "Verify your email before accepting an invitation")
    if await is_owner(actor, session):
        raise HTTPException(403, "Use a worker account to accept this invitation")
    name = body.display_name.strip()
    if not name:
        raise HTTPException(422, "Enter your name")
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    invitation = await session.scalar(select(WorkerInvitation).where(
        WorkerInvitation.token_hash == digest))
    if invitation is None or invitation.revoked or invitation.consumed or aware(invitation.expires_at) <= now():
        raise HTTPException(404, "Invitation is invalid, expired or already used")
    if invitation.email != actor.email.strip().casefold():
        raise HTTPException(403, "Sign in with the email this invitation was sent to")
    partner = await session.scalar(select(Partner).where(Partner.id == invitation.partner_id).with_for_update())
    if not partner or not partner.access_approved:
        raise HTTPException(403, "This business is not approved for access")
    invitation = await session.scalar(select(WorkerInvitation).where(WorkerInvitation.token_hash == digest)
        .with_for_update().execution_options(populate_existing=True))
    if invitation.revoked or invitation.consumed or aware(invitation.expires_at) <= now():
        raise HTTPException(404, "Invitation is invalid, expired or already used")
    member = await session.get(Member, (partner.id, actor.user_id))
    if member and member.role != "worker":
        raise HTTPException(409, "This account is already a business administrator")
    if member is None:
        member = Member(partner_id=partner.id, user_id=actor.user_id, role="worker")
        session.add(member)
    member.active = True
    member.display_name = name
    invitation.consumed = True
    invitation.accepted_by = actor.user_id
    await session.commit()
    return {"partner_id": partner.id, "business_name": partner.name, "role": "worker"}


async def scoped_conversation(partner_id, conversation_id, actor, session, lock=False):
    await accessible_partner(partner_id, actor, session, allow_worker=True, lock=lock)
    query = select(Conversation, Lead).join(Campaign, Campaign.id == Conversation.campaign_id).join(
        Lead, Lead.id == Conversation.lead_id).where(Campaign.partner_id == partner_id,
        Lead.partner_id == partner_id, Conversation.id == conversation_id)
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).first()
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


@router.get("/partners/{partner_id}/incoming-leads")
async def inbox(partner_id: uuid.UUID, actor: User, session: Session,
                limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    await accessible_partner(partner_id, actor, session, allow_worker=True)
    rows = (await session.execute(select(Conversation, Lead).join(
        Campaign, Campaign.id == Conversation.campaign_id).join(Lead, Lead.id == Conversation.lead_id).where(
        Campaign.partner_id == partner_id, Lead.partner_id == partner_id,
        Conversation.status.in_(["interested", "handoff", "appointment"])).order_by(
        Conversation.updated_at.desc(), Conversation.id).limit(limit).offset(offset))).all()
    return [dict(id=c.id, name=lead.name, phone=lead.phone, email=lead.email, status=c.status,
                 revision=c.revision, source="manual_tracking") for c, lead in rows]


@router.get("/partners/{partner_id}/incoming-leads/{conversation_id}")
async def thread(partner_id: uuid.UUID, conversation_id: uuid.UUID, actor: User, session: Session):
    c, lead = await scoped_conversation(partner_id, conversation_id, actor, session)
    events = (await session.scalars(select(ConversationEvent).where(
        ConversationEvent.conversation_id == conversation_id).order_by(ConversationEvent.revision))).all()
    return dict(id=c.id, name=lead.name, phone=lead.phone, email=lead.email, status=c.status,
                revision=c.revision, source="manual_tracking",
                events=[dict(revision=e.revision, note=e.note, status=e.status) for e in events])


class FollowUp(Body):
    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=5, max_length=2000)
    status: Literal["interested", "handoff", "appointment", "closed"]


@router.post("/partners/{partner_id}/incoming-leads/{conversation_id}/notes")
async def follow_up(partner_id: uuid.UUID, conversation_id: uuid.UUID, body: FollowUp,
                    actor: User, session: Session):
    c, _ = await scoped_conversation(partner_id, conversation_id, actor, session, lock=True)
    partner = await session.get(Partner, partner_id)
    campaign = await session.get(Campaign, c.campaign_id)
    if partner.status == "paused" or campaign.status in ("paused", "archived") or campaign.owner_hold:
        raise HTTPException(409, "Follow-up is paused")
    if c.revision != body.expected_revision:
        raise HTTPException(409, "Conversation changed; refresh before saving")
    if len(body.note.strip()) < 5:
        raise HTTPException(422, "Add an internal note of at least five characters")
    previous_status = c.status
    c.revision += 1
    c.status = body.status
    c.updated_at = now()
    session.add(ConversationEvent(conversation_id=c.id, actor_id=actor.user_id,
        revision=c.revision, status=c.status, note=body.note.strip()))
    await notify_interested(session, partner_id, c, previous_status)
    await session.commit()
    return {"ok": True}


async def notify_interested(session, partner_id, conversation, previous_status):
    """Call in the originating transaction. Practice sessions must never call this."""
    if conversation.status not in ("interested", "handoff") or previous_status in ("interested", "handoff"):
        return
    members = (await session.scalars(select(Member).where(Member.partner_id == partner_id,
        Member.role == "worker", Member.active.is_(True)))).all()
    for member in members:
        existing = await session.scalar(select(WorkerNotification.id).where(
            WorkerNotification.user_id == member.user_id,
            WorkerNotification.conversation_id == conversation.id,
            WorkerNotification.event_revision == conversation.revision))
        if existing:
            continue
        notification = WorkerNotification(partner_id=partner_id, user_id=member.user_id,
            conversation_id=conversation.id, event_revision=conversation.revision)
        session.add(notification)
        await session.flush()
        devices = (await session.scalars(select(WorkerDevice).where(
            WorkerDevice.user_id == member.user_id, WorkerDevice.active.is_(True)))).all()
        for device in devices:
            session.add(WorkerPushJob(notification_id=notification.id, device_id=device.id))


@router.get("/me/worker-notifications")
async def notifications(actor: User, session: Session, limit: int = Query(100, ge=1, le=100),
                        offset: int = Query(0, ge=0)):
    rows = (await session.scalars(select(WorkerNotification).join(Member,
        (Member.partner_id == WorkerNotification.partner_id) & (Member.user_id == WorkerNotification.user_id))
        .join(Partner, Partner.id == Member.partner_id).where(WorkerNotification.user_id == actor.user_id,
        Member.role == "worker", Member.active.is_(True), Partner.access_approved.is_(True))
        .order_by(WorkerNotification.created_at.desc(), WorkerNotification.id).limit(limit).offset(offset))).all()
    return [dict(id=n.id, partner_id=n.partner_id, conversation_id=n.conversation_id,
                 read=n.read, title="A lead needs follow-up") for n in rows]


@router.post("/me/worker-notifications/{notification_id}/read")
async def read_notification(notification_id: uuid.UUID, actor: User, session: Session):
    n = await session.get(WorkerNotification, notification_id)
    if n is None or n.user_id != actor.user_id:
        raise HTTPException(404, "Notification not found")
    await accessible_partner(n.partner_id, actor, session, allow_worker=True)
    n.read = True
    await session.commit()
    return {"ok": True}


class Device(Body):
    token: str = Field(pattern=r"^[0-9a-fA-F]{64,200}$")
    environment: Literal["sandbox", "production"]


@router.post("/me/worker-devices")
async def register_device(body: Device, actor: User, session: Session):
    member = await session.scalar(select(Member).join(Partner).where(Member.user_id == actor.user_id,
        Member.active.is_(True), Member.role == "worker", Partner.access_approved.is_(True)).limit(1))
    if not member:
        raise HTTPException(403, "Active worker access required")
    # Serialize registration per token, including first registration and account switching.
    token = body.token.lower()
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int(hashlib.sha256(token.encode()).hexdigest()[:15], 16)})
    device = await session.scalar(select(WorkerDevice).where(WorkerDevice.token == token).with_for_update())
    if device is None:
        device = WorkerDevice(token=token)
        session.add(device)
    device.user_id = actor.user_id
    device.environment = body.environment
    device.active = True
    await session.commit()
    return {"ok": True}


@router.delete("/me/worker-devices/{token}")
async def unregister_device(token: str, actor: User, session: Session):
    device = await session.scalar(select(WorkerDevice).where(WorkerDevice.token == token.lower(),
        WorkerDevice.user_id == actor.user_id).with_for_update())
    if device:
        device.active = False
    await session.commit()
    return {"ok": True}
