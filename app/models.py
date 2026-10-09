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
    status: Mapped[str] = mapped_column(String(30), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    onboarding: Mapped[dict] = mapped_column(JSON, default=dict)
    readiness: Mapped[dict] = mapped_column(JSON, default=dict)
    paused_from: Mapped[str | None] = mapped_column(String(30), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Member(Base):
    __tablename__ = "cc_partner_members"
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class Event(Base):
    __tablename__ = "cc_review_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cc_partners.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column()
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
    actor_id: Mapped[uuid.UUID] = mapped_column()
    file_name: Mapped[str] = mapped_column(String(200))
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
    actor_id: Mapped[uuid.UUID] = mapped_column()
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
    actor_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("conversation_id", "revision"),)
