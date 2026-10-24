import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "cc_owners"
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class Partner(Base):
    __tablename__ = "cc_partners"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    access_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    onboarding: Mapped[dict] = mapped_column(JSON, default=dict)
    readiness: Mapped[dict] = mapped_column(JSON, default=dict)
    paused_from: Mapped[str | None] = mapped_column(String(30), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Member(Base):
    __tablename__ = "cc_partner_members"
    role: Mapped[str] = mapped_column(String(20), default="admin")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class Event(Base):
    __tablename__ = "cc_review_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    previous_status: Mapped[str] = mapped_column(String(30))
    resulting_status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("partner_id", "revision"),)


class LeadImport(Base):
    __tablename__ = "cc_lead_imports"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    file_name: Mapped[str] = mapped_column(String(200))
    raw_rows: Mapped[list] = mapped_column(JSON, default=list)
    total_rows: Mapped[int] = mapped_column(Integer)
    imported: Mapped[int] = mapped_column(Integer)
    duplicates: Mapped[int] = mapped_column(Integer)
    invalid: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Lead(Base):
    __tablename__ = "cc_leads"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_lead_imports.id"))
    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(30), default="unreviewed")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    sms_permission: Mapped[str] = mapped_column(String(20), default="unknown")
    permission_evidence: Mapped[str] = mapped_column(Text, default="")
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("partner_id", "phone"),)


class Campaign(Base):
    __tablename__ = "cc_campaigns"
    owner_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSON)
    prepared_partner_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prepared_lead_revisions: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CampaignEvent(Base):
    __tablename__ = "cc_campaign_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_campaigns.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("campaign_id", "revision"),)


class Conversation(Base):
    __tablename__ = "cc_conversations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_campaigns.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_leads.id"))
    status: Mapped[str] = mapped_column(String(30), default="new")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("campaign_id", "lead_id"),)


class ConversationEvent(Base):
    __tablename__ = "cc_conversation_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_conversations.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("conversation_id", "revision"),)


class AITestSession(Base):
    __tablename__ = "cc_ai_test_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cc_campaigns.id"), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict] = mapped_column(JSON)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    decisions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessApplication(Base):
    __tablename__ = "cc_business_applications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    business_name: Mapped[str] = mapped_column(String(160))
    contact_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    phone: Mapped[str] = mapped_column(String(40))
    referral: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cc_partners.id"), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerInvitation(Base):
    __tablename__ = "cc_worker_invitations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkerNotification(Base):
    __tablename__ = "cc_worker_notifications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_conversations.id", ondelete="CASCADE"))
    event_revision: Mapped[int] = mapped_column(Integer)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("user_id", "conversation_id", "event_revision"),)


class WorkerDevice(Base):
    __tablename__ = "cc_worker_devices"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token: Mapped[str] = mapped_column(String(200), unique=True)
    environment: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkerPushJob(Base):
    __tablename__ = "cc_worker_push_jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_worker_notifications.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_worker_devices.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("notification_id", "device_id"),)


class CalendarConnection(Base):
    __tablename__ = "cc_google_calendar_connections"
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id", ondelete="CASCADE"), primary_key=True)
    refresh_token: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calendar_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class CalendarOAuthState(Base):
    __tablename__ = "cc_google_calendar_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column()
    verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
