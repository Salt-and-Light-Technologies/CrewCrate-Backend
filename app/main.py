import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from supabase import create_client

from .config import get_settings
from .database import engine, get_session
from .dependencies import Actor, current_actor, workspace_id
from .models import Activity, Appointment, Base, CRMNote, CRMTask, Client, FinancialDocument, LineItem, MediaAttachment, Workspace, WorkspaceMember
from .schemas import (ActivityCreate, ActivityRead, AppointmentCreate, AppointmentRead, AppointmentUpdate, ClientCreate, ClientRead, ClientUpdate, DocumentCreate, DocumentRead, DocumentUpdate, MediaDownload, MediaRead, NoteCreate, NoteRead, TaskCreate, TaskRead, TaskUpdate, WorkspaceCreate, WorkspaceRead)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().auto_create_schema:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="CrewCrate API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)


def scope(model, workspace: uuid.UUID): return select(model).where(model.workspace_id == workspace)
async def one(session, model, entity_id, workspace):
    entity = await session.scalar(scope(model, workspace).where(model.id == entity_id))
    if not entity: raise HTTPException(status_code=404, detail="Not found")
    return entity
async def ensure_client(session, client_id, workspace):
    if client_id is not None:
        await one(session, Client, client_id, workspace)
def apply(entity, data: dict):
    for key, value in data.items(): setattr(entity, key, value)
def document_read(document: FinancialDocument) -> dict:
    value = DocumentRead.model_validate(document).model_dump()
    value["total"] = sum(item.quantity * item.unit_price for item in document.line_items)
    return value


@app.get("/health")
async def health(): return {"status": "ok"}

@app.post("/v1/workspaces", response_model=WorkspaceRead, status_code=201)
async def create_workspace(payload: WorkspaceCreate, actor: Actor = Depends(current_actor), session: AsyncSession = Depends(get_session)):
    workspace = Workspace(name=payload.name); session.add(workspace); await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=actor.user_id, role="owner")); await session.commit(); await session.refresh(workspace)
    return workspace

@app.get("/v1/clients", response_model=list[ClientRead])
async def list_clients(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(scope(Client, workspace).order_by(Client.last_activity_at.desc()))).all()
@app.post("/v1/clients", response_model=ClientRead, status_code=201)
async def create_client(payload: ClientCreate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    client = Client(workspace_id=workspace, **payload.model_dump()); session.add(client); await session.commit(); await session.refresh(client); return client
@app.patch("/v1/clients/{client_id}", response_model=ClientRead)
async def update_client(client_id: uuid.UUID, payload: ClientUpdate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    client = await one(session, Client, client_id, workspace); apply(client, payload.model_dump()); await session.commit(); await session.refresh(client); return client
@app.delete("/v1/clients/{client_id}", status_code=204)
async def delete_client(client_id: uuid.UUID, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    await session.delete(await one(session, Client, client_id, workspace)); await session.commit()


@app.get("/v1/activities", response_model=list[ActivityRead])
async def list_activities(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(scope(Activity, workspace).order_by(Activity.occurred_at.desc()))).all()
@app.post("/v1/activities", response_model=ActivityRead, status_code=201)
async def create_activity(payload: ActivityCreate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    data = payload.model_dump(); await ensure_client(session, data["client_id"], workspace); data["occurred_at"] = data["occurred_at"] or datetime.now().astimezone(); entity = Activity(workspace_id=workspace, **data); session.add(entity); await session.commit(); await session.refresh(entity); return entity


@app.get("/v1/appointments", response_model=list[AppointmentRead])
async def list_appointments(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(scope(Appointment, workspace).order_by(Appointment.start_date))).all()
@app.post("/v1/appointments", response_model=AppointmentRead, status_code=201)
async def create_appointment(payload: AppointmentCreate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    data = payload.model_dump(); await ensure_client(session, data["client_id"], workspace); entity = Appointment(workspace_id=workspace, **data); session.add(entity); await session.commit(); await session.refresh(entity); return entity
@app.patch("/v1/appointments/{appointment_id}", response_model=AppointmentRead)
async def update_appointment(appointment_id: uuid.UUID, payload: AppointmentUpdate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    entity = await one(session, Appointment, appointment_id, workspace); data = payload.model_dump(); await ensure_client(session, data["client_id"], workspace); apply(entity, data); await session.commit(); await session.refresh(entity); return entity
@app.delete("/v1/appointments/{appointment_id}", status_code=204)
async def delete_appointment(appointment_id: uuid.UUID, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    await session.delete(await one(session, Appointment, appointment_id, workspace)); await session.commit()


@app.get("/v1/notes", response_model=list[NoteRead])
async def list_notes(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(scope(CRMNote, workspace).order_by(CRMNote.created_at.desc()))).all()
@app.post("/v1/notes", response_model=NoteRead, status_code=201)
async def create_note(payload: NoteCreate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    data = payload.model_dump(); await ensure_client(session, data["client_id"], workspace); entity = CRMNote(workspace_id=workspace, **data); session.add(entity); await session.commit(); await session.refresh(entity); return entity


@app.get("/v1/tasks", response_model=list[TaskRead])
async def list_tasks(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(scope(CRMTask, workspace).order_by(CRMTask.due_date))).all()
@app.post("/v1/tasks", response_model=TaskRead, status_code=201)
async def create_task(payload: TaskCreate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    data = payload.model_dump(); await ensure_client(session, data["client_id"], workspace); entity = CRMTask(workspace_id=workspace, **data); session.add(entity); await session.commit(); await session.refresh(entity); return entity
@app.patch("/v1/tasks/{task_id}", response_model=TaskRead)
async def update_task(task_id: uuid.UUID, payload: TaskUpdate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    entity = await one(session, CRMTask, task_id, workspace); data = payload.model_dump(); await ensure_client(session, data["client_id"], workspace); apply(entity, data); await session.commit(); await session.refresh(entity); return entity


@app.get("/v1/documents", response_model=list[DocumentRead])
async def list_documents(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    result = await session.scalars(scope(FinancialDocument, workspace).options(selectinload(FinancialDocument.line_items)).order_by(FinancialDocument.updated_at.desc()))
    return [document_read(item) for item in result.all()]
@app.post("/v1/documents", response_model=DocumentRead, status_code=201)
async def create_document(payload: DocumentCreate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    data = payload.model_dump(exclude={"line_items"}); await ensure_client(session, data["client_id"], workspace); entity = FinancialDocument(workspace_id=workspace, **data); entity.line_items = [LineItem(**line.model_dump()) for line in payload.line_items]; session.add(entity); await session.commit(); await session.refresh(entity, ["line_items"]); return document_read(entity)
@app.patch("/v1/documents/{document_id}", response_model=DocumentRead)
async def update_document(document_id: uuid.UUID, payload: DocumentUpdate, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    entity = await session.scalar(scope(FinancialDocument, workspace).options(selectinload(FinancialDocument.line_items)).where(FinancialDocument.id == document_id))
    if not entity: raise HTTPException(status_code=404, detail="Not found")
    data = payload.model_dump(exclude={"line_items"}); await ensure_client(session, data["client_id"], workspace); apply(entity, data); entity.line_items.clear(); entity.line_items.extend(LineItem(**line.model_dump()) for line in payload.line_items); await session.commit(); await session.refresh(entity, ["line_items"]); return document_read(entity)


@app.get("/v1/media", response_model=list[MediaRead])
async def list_media(workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(scope(MediaAttachment, workspace).order_by(MediaAttachment.created_at.desc()))).all()
@app.post("/v1/media", response_model=MediaRead, status_code=201)
async def upload_media(client_id: uuid.UUID | None = None, file: UploadFile = File(...), workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    await ensure_client(session, client_id, workspace)
    path = f"{workspace}/{uuid.uuid4()}-{file.filename or 'upload'}"; content = await file.read(); settings = get_settings()
    try: create_client(settings.supabase_url, settings.supabase_secret_key).storage.from_(settings.supabase_storage_bucket).upload(path, content, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as exc: raise HTTPException(status_code=502, detail="Media storage upload failed") from exc
    entity = MediaAttachment(workspace_id=workspace, client_id=client_id, name=file.filename or "Upload", media_type=file.content_type or "application/octet-stream", storage_path=path); session.add(entity); await session.commit(); await session.refresh(entity); return entity
@app.get("/v1/media/{media_id}/download-url", response_model=MediaDownload)
async def media_download_url(media_id: uuid.UUID, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    entity = await one(session, MediaAttachment, media_id, workspace); settings = get_settings()
    try:
        result = create_client(settings.supabase_url, settings.supabase_secret_key).storage.from_(settings.supabase_storage_bucket).create_signed_url(entity.storage_path, 3600)
        return {"signed_url": result["signedURL"]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Media URL could not be created") from exc
@app.delete("/v1/media/{media_id}", status_code=204)
async def delete_media(media_id: uuid.UUID, workspace: uuid.UUID = Depends(workspace_id), session: AsyncSession = Depends(get_session)):
    entity = await one(session, MediaAttachment, media_id, workspace); settings = get_settings()
    try:
        create_client(settings.supabase_url, settings.supabase_secret_key).storage.from_(settings.supabase_storage_bucket).remove([entity.storage_path])
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Media storage deletion failed") from exc
    await session.delete(entity); await session.commit()
