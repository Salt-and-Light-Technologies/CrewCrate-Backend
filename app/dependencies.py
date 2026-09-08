import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client as SupabaseClient, create_client

from .config import get_settings
from .database import get_session
from .models import WorkspaceMember


@dataclass
class Actor:
    user_id: uuid.UUID
    access_token: str


def supabase() -> SupabaseClient:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_publishable_key)


async def current_actor(authorization: str = Header(...)) -> Actor:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ")
    try:
        response = supabase().auth.get_user(token)
        return Actor(user_id=uuid.UUID(str(response.user.id)), access_token=token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc


async def workspace_id(
    x_workspace_id: uuid.UUID = Header(...), actor: Actor = Depends(current_actor), session: AsyncSession = Depends(get_session)
) -> uuid.UUID:
    membership = await session.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == x_workspace_id, WorkspaceMember.user_id == actor.user_id))
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied")
    return x_workspace_id
