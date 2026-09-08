import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Timestamped(Base):
    __abstract__ = True
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Workspace(Timestamped):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    members: Mapped[list["WorkspaceMember"]] = relationship(back_populates="workspace")


class WorkspaceMember(Timestamped):
    __tablename__ = "workspace_members"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)  # Supabase auth.users id
    role: Mapped[str] = mapped_column(String(30), default="member")
    workspace: Mapped[Workspace] = relationship(back_populates="members")


class WorkspaceScoped(Timestamped):
    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)


class Client(WorkspaceScoped):
    __tablename__ = "clients"
    name: Mapped[str] = mapped_column(String(180))
    company: Mapped[str] = mapped_column(String(180), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Activity(WorkspaceScoped):
    __tablename__ = "activities"
    title: Mapped[str] = mapped_column(String(220))
    detail: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    employee: Mapped[str] = mapped_column(String(160), default="You")
    status: Mapped[str] = mapped_column(String(30), default="new")
    type: Mapped[str] = mapped_column(String(50))
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    related_appointment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class Appointment(WorkspaceScoped):
    __tablename__ = "appointments"
    title: Mapped[str] = mapped_column(String(220))
    details: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)


class CRMNote(WorkspaceScoped):
    __tablename__ = "crm_notes"
    body: Mapped[str] = mapped_column(Text)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)


class CRMTask(WorkspaceScoped):
    __tablename__ = "crm_tasks"
    title: Mapped[str] = mapped_column(String(220))
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)


class FinancialDocument(WorkspaceScoped):
    __tablename__ = "financial_documents"
    title: Mapped[str] = mapped_column(String(220))
    kind: Mapped[str] = mapped_column(String(20))  # estimate or invoice
    status: Mapped[str] = mapped_column(String(30), default="new")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    line_items: Mapped[list["LineItem"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class LineItem(Base):
    __tablename__ = "line_items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financial_documents.id", ondelete="CASCADE"), index=True)
    item_description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    document: Mapped[FinancialDocument] = relationship(back_populates="line_items")


class MediaAttachment(WorkspaceScoped):
    __tablename__ = "media_attachments"
    name: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(80))
    storage_path: Mapped[str] = mapped_column(String(500), unique=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
