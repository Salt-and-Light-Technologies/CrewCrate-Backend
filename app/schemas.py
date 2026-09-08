import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["new", "scheduled", "quoted", "approved", "inProgress", "waiting", "completed", "paid", "cancelled"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    company: str = ""; email: str = ""; phone: str = ""; address: str = ""; notes: str = ""
    is_favorite: bool = False
class ClientUpdate(ClientCreate): pass
class ClientRead(ClientCreate, ORMModel):
    id: uuid.UUID; profile_image_path: str | None = None; created_at: datetime; updated_at: datetime; last_activity_at: datetime


class ActivityCreate(BaseModel):
    title: str; detail: str = ""; occurred_at: datetime | None = None; employee: str = "You"
    status: Status = "new"; type: str; client_id: uuid.UUID | None = None; related_appointment_id: uuid.UUID | None = None
class ActivityRead(ActivityCreate, ORMModel): id: uuid.UUID; created_at: datetime; updated_at: datetime


class AppointmentCreate(BaseModel):
    title: str; details: str = ""; start_date: datetime; end_date: datetime; status: Status = "scheduled"; client_id: uuid.UUID | None = None
class AppointmentUpdate(AppointmentCreate): pass
class AppointmentRead(AppointmentCreate, ORMModel): id: uuid.UUID; created_at: datetime; updated_at: datetime


class NoteCreate(BaseModel): body: str; client_id: uuid.UUID | None = None
class NoteRead(NoteCreate, ORMModel): id: uuid.UUID; created_at: datetime; updated_at: datetime
class TaskCreate(BaseModel): title: str; due_date: datetime; client_id: uuid.UUID | None = None
class TaskUpdate(TaskCreate): is_complete: bool = False
class TaskRead(TaskUpdate, ORMModel): id: uuid.UUID; created_at: datetime; updated_at: datetime


class LineItemInput(BaseModel): item_description: str; quantity: float = 1; unit_price: float = 0
class LineItemRead(LineItemInput, ORMModel): id: uuid.UUID
class DocumentCreate(BaseModel):
    title: str; kind: Literal["estimate", "invoice"]; status: Status = "new"; client_id: uuid.UUID | None = None
    line_items: list[LineItemInput] = []
class DocumentUpdate(DocumentCreate): pass
class DocumentRead(DocumentCreate, ORMModel):
    id: uuid.UUID; issued_at: datetime; created_at: datetime; updated_at: datetime
    line_items: list[LineItemRead] = []
    total: float = 0


class MediaRead(ORMModel):
    id: uuid.UUID; name: str; media_type: str; storage_path: str; client_id: uuid.UUID | None; created_at: datetime; updated_at: datetime
class MediaDownload(BaseModel): signed_url: str


class WorkspaceCreate(BaseModel): name: str = Field(min_length=1, max_length=160)
class WorkspaceRead(WorkspaceCreate, ORMModel): id: uuid.UUID; created_at: datetime; updated_at: datetime
