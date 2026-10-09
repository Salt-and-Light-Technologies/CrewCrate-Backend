import uuid
from dataclasses import dataclass

import httpx
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_session
from .models import Member, Owner, Partner


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID


async def current_actor(authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(401, "Bearer token required")
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                settings.supabase_url.rstrip("/") + "/auth/v1/user",
                headers={"apikey": settings.supabase_publishable_key, "Authorization": authorization},
            )
        if response.status_code in (401, 403):
            raise HTTPException(401, "Invalid access token")
        if response.status_code != 200:
            raise HTTPException(503, "Authentication service unavailable")
        return Actor(uuid.UUID(response.json()["id"]))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Authentication service unavailable") from None


async def is_owner(actor: Actor, session: AsyncSession) -> bool:
    return await session.get(Owner, actor.user_id) is not None


async def require_owner(
    actor: Actor = Depends(current_actor), session: AsyncSession = Depends(get_session)
) -> Actor:
    if not await is_owner(actor, session):
        raise HTTPException(403, "CrewCrate owner access required")
    return actor


async def accessible_partner(
    partner_id: uuid.UUID, actor: Actor, session: AsyncSession, *, lock=False
) -> Partner:
    # Do not reveal another tenant's existence. Owner elevation is provisioned outside this API.
    if not await is_owner(actor, session) and await session.get(Member, (partner_id, actor.user_id)) is None:
        raise HTTPException(404, "Partner not found")
    query = select(Partner).where(Partner.id == partner_id)
    if lock:
        query = query.with_for_update()
    partner = await session.scalar(query.execution_options(populate_existing=True))
    if partner is None:
        raise HTTPException(404, "Partner not found")
    return partner
