import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, current_actor
from .campaigns import get_campaign, revision
from .database import get_session
from .models import AITestSession, Campaign, CampaignEvent, Conversation, ConversationEvent, Event, LeadImport
from .workflow import record_event

router = APIRouter(prefix="/v1")
Session = Annotated[AsyncSession, Depends(get_session)]
User = Annotated[Actor, Depends(current_actor)]


@router.delete("/partners/{partner_id}/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(partner_id: uuid.UUID, campaign_id: uuid.UUID, actor: User,
                          session: Session, expected_revision: int = Query(ge=0)):
    partner, campaign = await get_campaign(partner_id, campaign_id, actor, session, lock=True)
    revision(campaign, expected_revision)
    if campaign.owner_hold:
        raise HTTPException(409, "This campaign is on hold. Contact the administrator before deleting it.")
    conversations = select_conversations(campaign_id)
    await session.execute(delete(ConversationEvent).where(ConversationEvent.conversation_id.in_(conversations)))
    await session.execute(delete(Conversation).where(Conversation.campaign_id == campaign_id))
    await session.execute(delete(AITestSession).where(AITestSession.campaign_id == campaign_id))
    await session.execute(delete(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id))
    await session.execute(delete(Campaign).where(Campaign.id == campaign_id))
    record_event(partner, actor, "campaign_deleted", partner.status, f"Deleted campaign {campaign.config.get('name', '')} ({campaign_id})", session)
    await session.commit()
    return Response(status_code=204)


def select_conversations(campaign_id):
    from sqlalchemy import select
    return select(Conversation.id).where(Conversation.campaign_id == campaign_id)


async def remove_auth_user(session, user_id):
    # Same PostgreSQL transaction as app cleanup; no client-side admin credentials.
    await session.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})


@router.delete("/me", status_code=204)
async def delete_account(actor: User, session: Session):
    await session.execute(delete(AITestSession).where(AITestSession.actor_id == actor.user_id))
    await session.execute(update(Event).where(Event.actor_id == actor.user_id).values(actor_id=None))
    await session.execute(update(LeadImport).where(LeadImport.actor_id == actor.user_id).values(actor_id=None))
    await remove_auth_user(session, actor.user_id)
    await session.commit()
    return Response(status_code=204)
